# Integrantes do grupo 9:
#   Thiago Costa    - Nº USP 14609529
#   Oliver Kuramae  - Nº USP 14601326
#   Theo Brito      - Nº USP 13688367

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from lexer import (
    CompileError,
    expand_copybooks,
    make_token_stream,
)
from parser import (
    AcceptStmt,
    AddStmt,
    BinaryExprNode,
    CloseStmt,
    ComputeStmt,
    ConditionNode,
    DataItemNode,
    DisplayStmt,
    DivideStmt,
    EvaluateStmt,
    ExprNode,
    FunctionCallNode,
    GoToStmt,
    IdentifierRef,
    IfStmt,
    InspectStmt,
    LiteralNode,
    LogicalConditionNode,
    MoveStmt,
    MultiplyStmt,
    NotConditionNode,
    OpenStmt,
    ParagraphNode,
    PerformStmt,
    ProcedureSectionNode,
    ProgramNode,
    ReadStmt,
    RelationConditionNode,
    SearchStmt,
    SelectNode,
    SentenceNode,
    SetStmt,
    SimpleConditionNode,
    StartStmt,
    StatementNode,
    StopRunStmt,
    SubtractStmt,
    UnaryExprNode,
    WriteStmt,
    parse,
)
from semantic_analyzer import analyze, classify_data_item

# ==============================================================================
# UTILITÁRIOS DE FORMATAÇÃO E ESCAPE PARA LLVM IR (.ll)
# ==============================================================================


def mangle_name(name: str) -> str:
    """Converte um identificador COBOL (ex: WRK-VALOR-1) para símbolo LLVM válido."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name.upper())
    if not sanitized or sanitized[0].isdigit():
        sanitized = "P_" + sanitized
    return sanitized


def llvm_escape_bytes(data: bytes) -> str:
    """Converte bytes para literal de string LLVM IR c\"...\" com escapes hexadecimais."""
    out: list[str] = []
    for b in data:
        if 32 <= b <= 126 and b not in (34, 92):  # exceto '"' (34) e '\' (92)
            out.append(chr(b))
        else:
            out.append(f"\\{b:02X}")
    return "".join(out)


def expand_pic_string(pic: str) -> str:
    """Expande repetições (n) de uma máscara PICTURE. Ex: 'S9(05)V9(02)' -> 'S99999V99'."""
    return re.sub(
        r"([9XASVZPB0/,.\-+*$])\(([0-9]+)\)",
        lambda m: m.group(1) * int(m.group(2)),
        pic.upper(),
    )


def analyze_pic_metrics(pic: str | None) -> tuple[int, int, bool, bool]:
    """
    Retorna (byte_size, scale_digits, is_signed, is_edited) a partir da máscara PIC.
    Em formato DISPLAY padrão do COBOL, 'V' e 'S' têm tamanho físico 0 bytes.
    """
    if not pic:
        return 1, 0, False, False

    expanded = expand_pic_string(pic)
    is_signed = "S" in expanded or expanded.startswith("-") or expanded.startswith("+")
    is_edited = any(c in expanded for c in "Z*.,+-/$")

    # Caracteres que ocupam 1 byte físico no buffer DISPLAY
    physical_chars = [c for c in expanded if c not in ("V", "S", "P")]
    byte_size = max(1, len(physical_chars))

    scale_digits = 0
    if "V" in expanded:
        after_v = expanded.split("V", 1)[1]
        scale_digits = sum(1 for c in after_v if c in "9Z*")
    elif "." in expanded:
        after_dot = expanded.split(".", 1)[1]
        scale_digits = sum(1 for c in after_dot if c in "9Z*")

    return byte_size, scale_digits, is_signed, is_edited


# ==============================================================================
# LAYOUT DE MEMÓRIA E TABELA DE DADOS PARA GERAÇÃO DE CÓDIGO
# ==============================================================================


@dataclass
class FieldLayout:
    name: str
    ancestors: tuple[str, ...]
    root_global: str            # Símbolo global LLVM (@var_<ROOT>)
    base_offset: int            # Deslocamento inicial em bytes dentro do buffer raiz
    elem_size: int              # Tamanho em bytes de uma única ocorrência do campo
    total_size: int             # elem_size * occurs_times
    category: str               # NUMERIC, ALPHABETIC, ALPHANUMERIC, GROUP, INDEX
    pic: str | None
    scale: int                  # Casas decimais após V ou .
    is_signed: bool
    is_edited: bool
    # Lista de (occurs_count, stride_bytes) da dimensão mais externa para a mais interna
    dimensions: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class RootBufferInfo:
    global_name: str
    size: int
    initial_bytes: bytearray


@dataclass
class FileRuntimeInfo:
    file_name: str
    handle_global: str          # @file_handle_<NAME> (ptr para FILE*)
    assign_target: str          # Nome literal do arquivo ou nome da variável COBOL
    assign_is_variable: bool
    status_var: str | None = None
    record_size: int = 64
    records: list[str] = field(default_factory=list)


# ==============================================================================
# GERADOR DE REPRESENTAÇÃO INTERMEDIÁRIA LLVM (LLVM IR)
# ==============================================================================


class LLVMIRGenerator:
    def __init__(self, program: ProgramNode):
        self.program: ProgramNode = program

        # Mapas de layout de memória da DATA DIVISION
        self.fields_by_name: dict[str, list[FieldLayout]] = {}
        self.root_buffers: list[RootBufferInfo] = []
        self.index_globals: dict[str, str] = {}  # nome_indice -> @idx_<NAME>
        self.files: dict[str, FileRuntimeInfo] = {}
        self.record_to_file: dict[str, str] = {}

        # Pool de constantes literais de string: bytes -> (@str.N, length)
        self.string_pool: dict[bytes, tuple[str, int]] = {}

        # Controle de parágrafos e seções da PROCEDURE DIVISION
        self.paragraph_Order: list[str] = []
        self.paragraph_functions: dict[str, str] = {}  # nome_cobol -> @para_<MANGLED>

        # Estado de emissão de instruções SSA na função atual
        self.reg_counter: int = 0
        self.label_counter: int = 0
        self.current_lines: list[str] = []
        self.block_terminated: bool = False

    # --- Contadores SSA e Emissão Básica ---

    def new_reg(self, prefix: str = "t") -> str:
        self.reg_counter += 1
        return f"%{prefix}{self.reg_counter}"

    def new_label(self, prefix: str = "lbl") -> str:
        self.label_counter += 1
        return f"{prefix}.{self.label_counter}"

    def emit(self, instruction: str) -> None:
        if self.block_terminated:
            # Inicia um bloco de continuação implícito para evitar instruções órfãs após terminador
            dead_lbl = self.new_label("unreachable.cont")
            self.current_lines.append(f"{dead_lbl}:")
            self.block_terminated = False
        self.current_lines.append(f"  {instruction}")

    def emit_terminator(self, instruction: str) -> None:
        if not self.block_terminated:
            self.current_lines.append(f"  {instruction}")
            self.block_terminated = True

    def emit_label(self, label: str) -> None:
        if not self.block_terminated:
            self.current_lines.append(f"  br label %{label}")
        self.current_lines.append(f"{label}:")
        self.block_terminated = False

    def intern_string(self, text: str, null_terminate: bool = True) -> tuple[str, int]:
        raw = text.encode("utf-8")
        if null_terminate:
            raw += b"\x00"
        if raw not in self.string_pool:
            sym = f"@.str.{len(self.string_pool)}"
            self.string_pool[raw] = (sym, len(raw))
        return self.string_pool[raw]

    # ==========================================================================
    # 1. CONSTRUÇÃO DO LAYOUT DE MEMÓRIA (ENVIRONMENT E DATA DIVISION)
    # ==========================================================================

    def build_layouts(self) -> None:
        # 1. Coleta arquivos do FILE-CONTROL (SELECT)
        env = self.program.environment_division
        if env and env.input_output_section:
            for sel in env.input_output_section.file_control:
                self._register_select(sel)

        # 2. Coleta FD e WORKING-STORAGE da DATA DIVISION
        data_div = self.program.data_division
        if data_div is None:
            return

        for fd in data_div.file_section:
            f_info = self.files.get(fd.file_name)
            for rec in fd.records:
                root_buf = self._build_root_item(rec)
                self.record_to_file[rec.name] = fd.file_name
                if f_info is not None:
                    f_info.record_size = max(f_info.record_size, root_buf.size)
                    f_info.records.append(rec.name)

        for item in data_div.working_storage:
            self._build_root_item(item)

        # Verifica se algum SELECT usa nome de variável em ASSIGN TO
        for f_info in self.files.values():
            if f_info.assign_target in self.fields_by_name:
                f_info.assign_is_variable = True

    def _register_select(self, sel: SelectNode) -> None:
        status_var: str | None = None
        for clause in sel.clauses:
            if clause.clause_type == "FILE_STATUS":
                status_var = clause.value
        handle_sym = f"@file_handle_{mangle_name(sel.file_name)}"
        self.files[sel.file_name] = FileRuntimeInfo(
            file_name=sel.file_name,
            handle_global=handle_sym,
            assign_target=sel.assign_to,
            assign_is_variable=False,
            status_var=status_var,
        )

    def _build_root_item(self, item: DataItemNode) -> RootBufferInfo:
        root_sym = f"@var_{mangle_name(item.name)}_{item.line}"
        elem_size = self._compute_item_elem_size(item)
        occurs_count = item.occurs.times if item.occurs and item.occurs.times > 0 else 1
        total_size = max(1, elem_size * occurs_count)

        # Buffer inicial preenchido com espaços (para grupos/alfanuméricos) ou '0' (para numéricos)
        cat = classify_data_item(item)
        fill_byte = ord("0") if cat == "NUMERIC" else ord(" ")
        init_buf = bytearray([fill_byte] * total_size)

        self._populate_field_layouts(
            item=item,
            ancestors=(),
            root_global=root_sym,
            current_offset=0,
            inherited_dims=[],
            init_buf=init_buf,
        )

        root_info = RootBufferInfo(
            global_name=root_sym,
            size=total_size,
            initial_bytes=init_buf,
        )
        self.root_buffers.append(root_info)
        return root_info

    def _compute_item_elem_size(self, item: DataItemNode) -> int:
        if item.level == 88:
            return 0
        non_88 = [c for c in item.children if c.level != 88]
        if non_88 and item.pic is None:
            sub_total = 0
            for child in non_88:
                c_elem = self._compute_item_elem_size(child)
                c_occ = child.occurs.times if child.occurs and child.occurs.times > 0 else 1
                sub_total += c_elem * c_occ
            return max(1, sub_total)
        byte_size, _, _, _ = analyze_pic_metrics(item.pic)
        return byte_size

    def _populate_field_layouts(
        self,
        item: DataItemNode,
        ancestors: tuple[str, ...],
        root_global: str,
        current_offset: int,
        inherited_dims: list[tuple[int, int]],
        init_buf: bytearray,
    ) -> int:
        if item.level == 88:
            return 0

        elem_size = self._compute_item_elem_size(item)
        occurs_count = item.occurs.times if item.occurs and item.occurs.times > 0 else 1
        total_size = elem_size * occurs_count

        dims = list(inherited_dims)
        if item.occurs and item.occurs.times > 0:
            dims.append((occurs_count, elem_size))
            for idx_name in item.occurs.indexed_by:
                if idx_name not in self.index_globals:
                    self.index_globals[idx_name] = f"@idx_{mangle_name(idx_name)}"

        cat = classify_data_item(item)
        _, scale, is_signed, is_edited = analyze_pic_metrics(item.pic)

        layout = FieldLayout(
            name=item.name,
            ancestors=ancestors,
            root_global=root_global,
            base_offset=current_offset,
            elem_size=elem_size,
            total_size=total_size,
            category=cat,
            pic=item.pic,
            scale=scale,
            is_signed=is_signed,
            is_edited=is_edited,
            dimensions=dims,
        )
        self.fields_by_name.setdefault(item.name, []).append(layout)

        non_88 = [c for c in item.children if c.level != 88]
        if non_88:
            child_offset = current_offset
            for child in non_88:
                child_span = self._populate_field_layouts(
                    item=child,
                    ancestors=ancestors + (item.name,),
                    root_global=root_global,
                    current_offset=child_offset,
                    inherited_dims=dims,
                    init_buf=init_buf,
                )
                child_offset += child_span
        else:
            # Inicializa os bytes do item elementar em todas as ocorrências das dimensões
            raw_val = self._format_initial_bytes(layout, item.value)
            self._write_initial_occurrences(
                init_buf=init_buf,
                base_offset=current_offset,
                dims=dims,
                dim_idx=0,
                raw_bytes=raw_val,
            )

        return total_size

    def _write_initial_occurrences(
        self,
        init_buf: bytearray,
        base_offset: int,
        dims: list[tuple[int, int]],
        dim_idx: int,
        raw_bytes: bytes,
    ) -> None:
        if dim_idx >= len(dims):
            end = min(len(init_buf), base_offset + len(raw_bytes))
            init_buf[base_offset:end] = raw_bytes[: end - base_offset]
            return

        count, stride = dims[dim_idx]
        for k in range(count):
            self._write_initial_occurrences(
                init_buf=init_buf,
                base_offset=base_offset + k * stride,
                dims=dims,
                dim_idx=dim_idx + 1,
                raw_bytes=raw_bytes,
            )

    @staticmethod
    def _format_initial_bytes(layout: FieldLayout, value_str: str | None) -> bytes:
        size = layout.elem_size
        if layout.category == "NUMERIC":
            num_val = 0.0
            if value_str is not None and value_str.upper() not in (
                "ZERO", "ZEROS", "ZEROES", "SPACE", "SPACES", "LOW-VALUE", "LOW-VALUES"
            ):
                try:
                    num_val = float(value_str)
                except ValueError:
                    num_val = 0.0
            scaled_int = int(round(abs(num_val) * (10 ** layout.scale)))
            digits = f"{scaled_int:0{size}d}"[-size:]
            return digits.encode("ascii")

        # Alfanumérico / Alfabético / Grupo
        if value_str is None or value_str.upper() in ("SPACE", "SPACES"):
            return b" " * size
        if value_str.upper() in ("ZERO", "ZEROS", "ZEROES"):
            return b"0" * size
        if value_str.upper() in ("LOW-VALUE", "LOW-VALUES"):
            return b"\x00" * size
        raw = value_str.encode("utf-8")[:size]
        return raw.ljust(size, b" ")

    # ==========================================================================
    # 2. RESOLUÇÃO DE REFERÊNCIAS DE DADOS E ENDEREÇAMENTO GEP
    # ==========================================================================

    def resolve_field(self, ref: IdentifierRef) -> FieldLayout | None:
        candidates = self.fields_by_name.get(ref.name, [])
        if not candidates:
            return None
        if len(candidates) == 1 or not ref.qualifiers:
            return candidates[0]
        for cand in candidates:
            rev_anc = list(reversed(cand.ancestors))
            idx = 0
            ok = True
            for q in ref.qualifiers:
                while idx < len(rev_anc) and rev_anc[idx] != q:
                    idx += 1
                if idx >= len(rev_anc):
                    ok = False
                    break
                idx += 1
            if ok:
                return cand
        return candidates[0]

    def emit_field_address_and_length(self, ref: IdentifierRef) -> tuple[str, int, FieldLayout | None]:
        """
        Emite instruções LLVM IR para calcular o ponteiro (ptr) de um identificador
        (considerando subscritos 1-indexados em tabelas OCCURS) e retorna (ptr_reg, byte_len, layout).
        """
        layout = self.resolve_field(ref)
        if layout is None:
            dummy_sym, _ = self.intern_string("", null_terminate=True)
            return dummy_sym, 1, None

        if not ref.subscripts or not layout.dimensions:
            ptr_reg = self.new_reg("ptr")
            self.emit(
                f"{ptr_reg} = getelementptr inbounds i8, ptr {layout.root_global}, i64 {layout.base_offset}"
            )
            return ptr_reg, layout.elem_size, layout

        # Calcula deslocamento dinâmico para subscritos (i_k - 1) * stride_k
        offset_reg = self.new_reg("ofs")
        self.emit(f"{offset_reg} = add i64 0, {layout.base_offset}")

        num_subs = min(len(ref.subscripts), len(layout.dimensions))
        for k in range(num_subs):
            _, stride = layout.dimensions[k]
            sub_val_dbl = self.emit_numeric_expr(ref.subscripts[k])
            sub_idx_i64 = self.new_reg("idx")
            self.emit(f"{sub_idx_i64} = fptosi double {sub_val_dbl} to i64")
            zero_based = self.new_reg("zb")
            self.emit(f"{zero_based} = sub i64 {sub_idx_i64}, 1")
            scaled_ofs = self.new_reg("sofs")
            self.emit(f"{scaled_ofs} = mul i64 {zero_based}, {stride}")
            new_offset_reg = self.new_reg("ofs")
            self.emit(f"{new_offset_reg} = add i64 {offset_reg}, {scaled_ofs}")
            offset_reg = new_offset_reg

        ptr_reg = self.new_reg("ptr")
        self.emit(
            f"{ptr_reg} = getelementptr inbounds i8, ptr {layout.root_global}, i64 {offset_reg}"
        )
        return ptr_reg, layout.elem_size, layout

    # ==========================================================================
    # 3. AVALIAÇÃO DE EXPRESSÕES E CONDIÇÕES EM LLVM IR
    # ==========================================================================

    def emit_numeric_expr(self, expr: ExprNode | None) -> str:
        """Avalia uma expressão numérica e retorna o registrador SSA do tipo double."""
        if expr is None:
            reg = self.new_reg("zero")
            self.emit(f"{reg} = fadd double 0.0, 0.0")
            return reg

        if isinstance(expr, LiteralNode):
            val = 0.0
            if expr.literal_type == "NUMBER":
                try:
                    val = float(expr.value)
                except ValueError:
                    val = 0.0
            elif expr.literal_type == "ALPHANUMERIC_LITERAL":
                try:
                    val = float(expr.value.strip())
                except ValueError:
                    val = 0.0
            reg = self.new_reg("num")
            self.emit(f"{reg} = fadd double 0.0, {val:.8e}")
            return reg

        if isinstance(expr, IdentifierRef):
            # Verifica se é um índice implícito (INDEXED BY)
            if expr.name in self.index_globals:
                idx_sym = self.index_globals[expr.name]
                iv = self.new_reg("idxv")
                self.emit(f"{iv} = load i64, ptr {idx_sym}")
                dv = self.new_reg("idxd")
                self.emit(f"{dv} = sitofp i64 {iv} to double")
                return dv

            ptr_reg, length, layout = self.emit_field_address_and_length(expr)
            scale = layout.scale if layout else 0
            res = self.new_reg("val")
            self.emit(
                f"{res} = call double @__cobol_read_num(ptr {ptr_reg}, i64 {length}, i64 {scale})"
            )
            return res

        if isinstance(expr, UnaryExprNode):
            op_reg = self.emit_numeric_expr(expr.operand)
            if expr.operator == "-":
                neg_reg = self.new_reg("neg")
                self.emit(f"{neg_reg} = fneg double {op_reg}")
                return neg_reg
            return op_reg

        if isinstance(expr, BinaryExprNode):
            lhs = self.emit_numeric_expr(expr.left)
            rhs = self.emit_numeric_expr(expr.right)
            res = self.new_reg("bin")
            match expr.operator:
                case "+":
                    self.emit(f"{res} = fadd double {lhs}, {rhs}")
                case "-":
                    self.emit(f"{res} = fsub double {lhs}, {rhs}")
                case "*":
                    self.emit(f"{res} = fmul double {lhs}, {rhs}")
                case "/":
                    self.emit(f"{res} = fdiv double {lhs}, {rhs}")
                case "**":
                    self.emit(f"{res} = call double @llvm.pow.f64(double {lhs}, double {rhs})")
                case _:
                    self.emit(f"{res} = fadd double {lhs}, {rhs}")
            return res

        reg = self.new_reg("zero")
        self.emit(f"{reg} = fadd double 0.0, 0.0")
        return reg

    def emit_store_numeric(self, val_dbl_reg: str, target_ref: IdentifierRef) -> None:
        """Armazena um registrador SSA double em uma variável COBOL ou índice."""
        if target_ref.name in self.index_globals:
            idx_sym = self.index_globals[target_ref.name]
            iv = self.new_reg("idx_store")
            self.emit(f"{iv} = fptosi double {val_dbl_reg} to i64")
            self.emit(f"store i64 {iv}, ptr {idx_sym}")
            return

        ptr_reg, length, layout = self.emit_field_address_and_length(target_ref)
        if layout is None:
            return
        scale = layout.scale
        is_ed = 1 if layout.is_edited else 0
        self.emit(
            f"call void @__cobol_write_num(double {val_dbl_reg}, ptr {ptr_reg}, i64 {length}, i64 {scale}, i32 {is_ed})"
        )

    def emit_string_slice(self, expr: ExprNode | None) -> tuple[str, int]:
        """Retorna (ptr_reg, byte_len) para um operando textual/alfanumérico."""
        if expr is None:
            sym, length = self.intern_string(" ", null_terminate=False)
            return sym, length

        if isinstance(expr, LiteralNode):
            if expr.literal_type == "FIGURATIVE_CONSTANT":
                up = expr.value.upper()
                if up in ("ZERO", "ZEROS", "ZEROES"):
                    sym, length = self.intern_string("0", null_terminate=False)
                    return sym, length
                if up in ("LOW-VALUE", "LOW-VALUES"):
                    sym, length = self.intern_string("\x00", null_terminate=False)
                    return sym, length
                sym, length = self.intern_string(" ", null_terminate=False)
                return sym, length
            sym, length = self.intern_string(expr.value, null_terminate=False)
            return sym, length

        if isinstance(expr, IdentifierRef):
            ptr_reg, length, _ = self.emit_field_address_and_length(expr)
            return ptr_reg, length

        sym, length = self.intern_string(" ", null_terminate=False)
        return sym, length

    def is_alphanumeric_expr(self, expr: ExprNode | None) -> bool:
        if isinstance(expr, LiteralNode):
            if expr.literal_type == "ALPHANUMERIC_LITERAL":
                return True
            if expr.literal_type == "FIGURATIVE_CONSTANT" and expr.value.upper() in (
                "SPACE", "SPACES", "LOW-VALUE", "LOW-VALUES", "HIGH-VALUE", "HIGH-VALUES"
            ):
                return True
            return False
        if isinstance(expr, IdentifierRef):
            if expr.name in self.index_globals:
                return False
            layout = self.resolve_field(expr)
            if layout and layout.category in ("ALPHANUMERIC", "ALPHABETIC", "GROUP"):
                return True
        if isinstance(expr, FunctionCallNode):
            return True
        return False

    def emit_condition(self, cond: ConditionNode | None) -> str:
        """Avalia uma ConditionNode e retorna um registrador SSA booleano (i1)."""
        if cond is None:
            r = self.new_reg("cond")
            self.emit(f"{r} = icmp eq i32 1, 1")
            return r

        if isinstance(cond, SimpleConditionNode):
            val_reg = self.emit_numeric_expr(cond.operand)
            r = self.new_reg("scond")
            self.emit(f"{r} = fcmp one double {val_reg}, 0.0")
            return r

        if isinstance(cond, NotConditionNode):
            inner = self.emit_condition(cond.operand)
            r = self.new_reg("notc")
            self.emit(f"{r} = xor i1 {inner}, true")
            return r

        if isinstance(cond, LogicalConditionNode):
            lhs = self.emit_condition(cond.left)
            rhs = self.emit_condition(cond.right)
            r = self.new_reg("logc")
            op = "and" if cond.operator == "AND" else "or"
            self.emit(f"{r} = {op} i1 {lhs}, {rhs}")
            return r

        if isinstance(cond, RelationConditionNode):
            use_alpha = self.is_alphanumeric_expr(cond.left) or self.is_alphanumeric_expr(cond.right)
            if use_alpha:
                p1, l1 = self.emit_string_slice(cond.left)
                p2, l2 = self.emit_string_slice(cond.right)
                cmp_res = self.new_reg("scmp")
                self.emit(
                    f"{cmp_res} = call i32 @__cobol_cmp_alpha(ptr {p1}, i64 {l1}, ptr {p2}, i64 {l2})"
                )
                r = self.new_reg("rcmp")
                ic = self._map_icmp_predicate(cond.operator)
                self.emit(f"{r} = icmp {ic} i32 {cmp_res}, 0")
                return r
            else:
                v1 = self.emit_numeric_expr(cond.left)
                v2 = self.emit_numeric_expr(cond.right)
                r = self.new_reg("fcmp")
                fc = self._map_fcmp_predicate(cond.operator)
                self.emit(f"{r} = fcmp {fc} double {v1}, {v2}")
                return r

        r = self.new_reg("cond")
        self.emit(f"{r} = icmp eq i32 1, 1")
        return r

    @staticmethod
    def _map_icmp_predicate(op: str) -> str:
        op_clean = op.upper().strip()
        match op_clean:
            case "=" | "EQUAL":
                return "eq"
            case "NOT =" | "NOT EQUAL":
                return "ne"
            case ">" | "GREATER":
                return "sgt"
            case "<" | "LESS":
                return "slt"
            case ">=" | "GREATER OR EQUAL" | "NOT <" | "NOT LESS":
                return "sge"
            case "<=" | "LESS OR EQUAL" | "NOT >" | "NOT GREATER":
                return "sle"
            case _:
                return "eq"

    @staticmethod
    def _map_fcmp_predicate(op: str) -> str:
        op_clean = op.upper().strip()
        match op_clean:
            case "=" | "EQUAL":
                return "oeq"
            case "NOT =" | "NOT EQUAL":
                return "one"
            case ">" | "GREATER":
                return "ogt"
            case "<" | "LESS":
                return "olt"
            case ">=" | "GREATER OR EQUAL" | "NOT <" | "NOT LESS":
                return "oge"
            case "<=" | "LESS OR EQUAL" | "NOT >" | "NOT GREATER":
                return "ole"
            case _:
                return "oeq"

    # ==========================================================================
    # 4. TRADUÇÃO DOS STATEMENTS DA PROCEDURE DIVISION
    # ==========================================================================

    def emit_statement(self, stmt: StatementNode) -> None:
        if isinstance(stmt, DisplayStmt):
            self._emit_display(stmt)
        elif isinstance(stmt, AcceptStmt):
            self._emit_accept(stmt)
        elif isinstance(stmt, MoveStmt):
            self._emit_move(stmt)
        elif isinstance(stmt, PerformStmt):
            self._emit_perform(stmt)
        elif isinstance(stmt, StopRunStmt):
            self.emit("call void @exit(i32 0)")
            self.emit_terminator("unreachable")
        elif isinstance(stmt, GoToStmt):
            fn = self.paragraph_functions.get(stmt.target)
            if fn:
                self.emit(f"call void {fn}()")
            self.emit_terminator("ret void")
        elif isinstance(stmt, IfStmt):
            self._emit_if(stmt)
        elif isinstance(stmt, AddStmt):
            self._emit_add(stmt)
        elif isinstance(stmt, SubtractStmt):
            self._emit_subtract(stmt)
        elif isinstance(stmt, MultiplyStmt):
            self._emit_multiply(stmt)
        elif isinstance(stmt, DivideStmt):
            self._emit_divide(stmt)
        elif isinstance(stmt, ComputeStmt):
            self._emit_compute(stmt)
        elif isinstance(stmt, EvaluateStmt):
            self._emit_evaluate(stmt)
        elif isinstance(stmt, SearchStmt):
            self._emit_search(stmt)
        elif isinstance(stmt, SetStmt):
            self._emit_set(stmt)
        elif isinstance(stmt, InspectStmt):
            self._emit_inspect(stmt)
        elif isinstance(stmt, OpenStmt):
            self._emit_open(stmt)
        elif isinstance(stmt, CloseStmt):
            self._emit_close(stmt)
        elif isinstance(stmt, ReadStmt):
            self._emit_read(stmt)
        elif isinstance(stmt, WriteStmt):
            self._emit_write(stmt)
        elif isinstance(stmt, StartStmt):
            self._emit_start(stmt)

    def _emit_display(self, stmt: DisplayStmt) -> None:
        fmt_slice, _ = self.intern_string("%.*s")
        fmt_int, _ = self.intern_string("%ld")

        for op in stmt.operands:
            if isinstance(op, IdentifierRef) and op.name in self.index_globals:
                idx_sym = self.index_globals[op.name]
                iv = self.new_reg("idxv")
                self.emit(f"{iv} = load i64, ptr {idx_sym}")
                c_reg = self.new_reg("pr")
                self.emit(f"{c_reg} = call i32 (ptr, ...) @printf(ptr {fmt_int}, i64 {iv})")
            elif isinstance(op, LiteralNode):
                s_sym, s_len = self.intern_string(op.value, null_terminate=False)
                c_reg = self.new_reg("pr")
                self.emit(
                    f"{c_reg} = call i32 (ptr, ...) @printf(ptr {fmt_slice}, i32 {s_len}, ptr {s_sym})"
                )
            elif isinstance(op, IdentifierRef):
                ptr_reg, length, _ = self.emit_field_address_and_length(op)
                c_reg = self.new_reg("pr")
                self.emit(
                    f"{c_reg} = call i32 (ptr, ...) @printf(ptr {fmt_slice}, i32 {length}, ptr {ptr_reg})"
                )
            else:
                val_dbl = self.emit_numeric_expr(op)
                fmt_num, _ = self.intern_string("%g")
                c_reg = self.new_reg("pr")
                self.emit(f"{c_reg} = call i32 (ptr, ...) @printf(ptr {fmt_num}, double {val_dbl})")

        if not stmt.no_advancing:
            nl_reg = self.new_reg("nl")
            self.emit(f"{nl_reg} = call i32 @putchar(i32 10)")

    def _emit_accept(self, stmt: AcceptStmt) -> None:
        if stmt.target is None:
            return
        ptr_reg, length, layout = self.emit_field_address_and_length(stmt.target)
        if layout is None:
            return
        is_num = 1 if layout.category == "NUMERIC" else 0
        is_ed = 1 if layout.is_edited else 0
        self.emit(
            f"call void @__cobol_accept(ptr {ptr_reg}, i64 {length}, i64 {layout.scale}, i32 {is_num}, i32 {is_ed})"
        )

    def _emit_move(self, stmt: MoveStmt) -> None:
        if stmt.source is None:
            return

        # Caso especial: MOVE FUNCTION UPPER-CASE(arg) TO targets
        if isinstance(stmt.source, FunctionCallNode) and stmt.source.function_name == "UPPER-CASE":
            arg = stmt.source.arguments[0] if stmt.source.arguments else None
            src_ptr, src_len = self.emit_string_slice(arg)
            for target_ref in stmt.targets:
                dst_ptr, dst_len, _ = self.emit_field_address_and_length(target_ref)
                self.emit(
                    f"call void @__cobol_move_alpha(ptr {dst_ptr}, i64 {dst_len}, ptr {src_ptr}, i64 {src_len})"
                )
                self.emit(f"call void @__cobol_upper_inplace(ptr {dst_ptr}, i64 {dst_len})")
            return

        for target_ref in stmt.targets:
            if target_ref.name in self.index_globals:
                val_dbl = self.emit_numeric_expr(stmt.source)
                self.emit_store_numeric(val_dbl, target_ref)
                continue

            dst_ptr, dst_len, dst_layout = self.emit_field_address_and_length(target_ref)
            if dst_layout is None:
                continue

            # Constantes figurativas (LOW-VALUES, SPACES, ZEROS)
            if isinstance(stmt.source, LiteralNode) and stmt.source.literal_type == "FIGURATIVE_CONSTANT":
                up = stmt.source.value.upper()
                if up in ("LOW-VALUE", "LOW-VALUES"):
                    self.emit(
                        f"call void @llvm.memset.p0.i64(ptr {dst_ptr}, i8 0, i64 {dst_len}, i1 false)"
                    )
                    continue
                if up in ("SPACE", "SPACES"):
                    self.emit(
                        f"call void @llvm.memset.p0.i64(ptr {dst_ptr}, i8 32, i64 {dst_len}, i1 false)"
                    )
                    continue
                if up in ("ZERO", "ZEROS", "ZEROES"):
                    if dst_layout.category == "NUMERIC":
                        z_reg = self.new_reg("zero")
                        self.emit(f"{z_reg} = fadd double 0.0, 0.0")
                        self.emit_store_numeric(z_reg, target_ref)
                    else:
                        self.emit(
                            f"call void @llvm.memset.p0.i64(ptr {dst_ptr}, i8 48, i64 {dst_len}, i1 false)"
                        )
                    continue

            if dst_layout.category == "NUMERIC":
                val_dbl = self.emit_numeric_expr(stmt.source)
                self.emit_store_numeric(val_dbl, target_ref)
            else:
                if self.is_alphanumeric_expr(stmt.source):
                    src_ptr, src_len = self.emit_string_slice(stmt.source)
                    self.emit(
                        f"call void @__cobol_move_alpha(ptr {dst_ptr}, i64 {dst_len}, ptr {src_ptr}, i64 {src_len})"
                    )
                elif isinstance(stmt.source, IdentifierRef) and stmt.source.name not in self.index_globals:
                    # Copia a representação textual do campo numérico para o campo alfanumérico
                    src_ptr, src_len, _ = self.emit_field_address_and_length(stmt.source)
                    self.emit(
                        f"call void @__cobol_move_alpha(ptr {dst_ptr}, i64 {dst_len}, ptr {src_ptr}, i64 {src_len})"
                    )
                else:
                    val_dbl = self.emit_numeric_expr(stmt.source)
                    is_ed = 1 if dst_layout.is_edited else 0
                    self.emit(
                        f"call void @__cobol_write_num(double {val_dbl}, ptr {dst_ptr}, i64 {dst_len}, i64 {dst_layout.scale}, i32 {is_ed})"
                    )

    def _emit_perform(self, stmt: PerformStmt) -> None:
        targets_to_call = self._resolve_perform_range(stmt.target, stmt.thru)

        if stmt.until is not None:
            cond_lbl = self.new_label("perform.cond")
            body_lbl = self.new_label("perform.body")
            end_lbl = self.new_label("perform.end")

            self.emit_label(cond_lbl)
            c_reg = self.emit_condition(stmt.until)
            self.emit_terminator(f"br i1 {c_reg}, label %{end_lbl}, label %{body_lbl}")

            self.emit_label(body_lbl)
            for fn in targets_to_call:
                self.emit(f"call void {fn}()")
            self.emit_terminator(f"br label %{cond_lbl}")

            self.emit_label(end_lbl)
        elif stmt.times is not None:
            cnt_ptr = self.new_reg("times_cnt")
            self.emit(f"{cnt_ptr} = alloca i64")
            n_dbl = self.emit_numeric_expr(stmt.times)
            n_i64 = self.new_reg("times_n")
            self.emit(f"{n_i64} = fptosi double {n_dbl} to i64")
            self.emit(f"store i64 {n_i64}, ptr {cnt_ptr}")

            cond_lbl = self.new_label("perform.times.cond")
            body_lbl = self.new_label("perform.times.body")
            end_lbl = self.new_label("perform.times.end")

            self.emit_label(cond_lbl)
            cur_v = self.new_reg("tc")
            self.emit(f"{cur_v} = load i64, ptr {cnt_ptr}")
            cmp_r = self.new_reg("tcmp")
            self.emit(f"{cmp_r} = icmp sgt i64 {cur_v}, 0")
            self.emit_terminator(f"br i1 {cmp_r}, label %{body_lbl}, label %{end_lbl}")

            self.emit_label(body_lbl)
            for fn in targets_to_call:
                self.emit(f"call void {fn}()")
            nxt_v = self.new_reg("tnxt")
            self.emit(f"{nxt_v} = sub i64 {cur_v}, 1")
            self.emit(f"store i64 {nxt_v}, ptr {cnt_ptr}")
            self.emit_terminator(f"br label %{cond_lbl}")

            self.emit_label(end_lbl)
        else:
            for fn in targets_to_call:
                self.emit(f"call void {fn}()")

    def _resolve_perform_range(self, start_name: str, thru_name: str | None) -> list[str]:
        if thru_name is None or start_name not in self.paragraph_Order or thru_name not in self.paragraph_Order:
            fn = self.paragraph_functions.get(start_name)
            return [fn] if fn else []
        i1 = self.paragraph_Order.index(start_name)
        i2 = self.paragraph_Order.index(thru_name)
        if i1 <= i2:
            names = self.paragraph_Order[i1 : i2 + 1]
        else:
            names = [start_name]
        return [self.paragraph_functions[n] for n in names if n in self.paragraph_functions]

    def _emit_if(self, stmt: IfStmt) -> None:
        cond_reg = self.emit_condition(stmt.condition)
        then_lbl = self.new_label("if.then")
        else_lbl = self.new_label("if.else")
        end_lbl = self.new_label("if.end")

        has_else = bool(stmt.else_body)
        false_target = else_lbl if has_else else end_lbl
        self.emit_terminator(f"br i1 {cond_reg}, label %{then_lbl}, label %{false_target}")

        self.emit_label(then_lbl)
        for s in stmt.then_body:
            self.emit_statement(s)
        if not self.block_terminated:
            self.emit_terminator(f"br label %{end_lbl}")

        if has_else:
            self.emit_label(else_lbl)
            for s in stmt.else_body:
                self.emit_statement(s)
            if not self.block_terminated:
                self.emit_terminator(f"br label %{end_lbl}")

        self.emit_label(end_lbl)

    def _emit_add(self, stmt: AddStmt) -> None:
        sum_reg = self.new_reg("add_acc")
        self.emit(f"{sum_reg} = fadd double 0.0, 0.0")
        for op in stmt.operands:
            op_v = self.emit_numeric_expr(op)
            nxt = self.new_reg("add_acc")
            self.emit(f"{nxt} = fadd double {sum_reg}, {op_v}")
            sum_reg = nxt

        if stmt.giving_targets:
            base_val = sum_reg
            if stmt.to_targets:
                to_v = self.emit_numeric_expr(stmt.to_targets[0])
                combined = self.new_reg("add_giv")
                self.emit(f"{combined} = fadd double {sum_reg}, {to_v}")
                base_val = combined
            for g_ref in stmt.giving_targets:
                self.emit_store_numeric(base_val, g_ref)
                self._check_and_emit_size_error(base_val, g_ref, stmt.on_size_error)
        else:
            for t_ref in stmt.to_targets:
                cur_v = self.emit_numeric_expr(t_ref)
                res_v = self.new_reg("add_to")
                self.emit(f"{res_v} = fadd double {cur_v}, {sum_reg}")
                self.emit_store_numeric(res_v, t_ref)
                self._check_and_emit_size_error(res_v, t_ref, stmt.on_size_error)

    def _emit_subtract(self, stmt: SubtractStmt) -> None:
        sub_reg = self.new_reg("sub_acc")
        self.emit(f"{sub_reg} = fadd double 0.0, 0.0")
        for op in stmt.operands:
            op_v = self.emit_numeric_expr(op)
            nxt = self.new_reg("sub_acc")
            self.emit(f"{nxt} = fadd double {sub_reg}, {op_v}")
            sub_reg = nxt

        if stmt.giving_targets and stmt.from_targets:
            from_v = self.emit_numeric_expr(stmt.from_targets[0])
            diff = self.new_reg("sub_giv")
            self.emit(f"{diff} = fsub double {from_v}, {sub_reg}")
            for g_ref in stmt.giving_targets:
                self.emit_store_numeric(diff, g_ref)
                self._check_and_emit_size_error(diff, g_ref, stmt.on_size_error)
        else:
            for t_ref in stmt.from_targets:
                cur_v = self.emit_numeric_expr(t_ref)
                diff = self.new_reg("sub_from")
                self.emit(f"{diff} = fsub double {cur_v}, {sub_reg}")
                self.emit_store_numeric(diff, t_ref)
                self._check_and_emit_size_error(diff, t_ref, stmt.on_size_error)

    def _emit_multiply(self, stmt: MultiplyStmt) -> None:
        op_v = self.emit_numeric_expr(stmt.operand)
        if stmt.giving_targets and stmt.by_targets:
            by_v = self.emit_numeric_expr(stmt.by_targets[0])
            prod = self.new_reg("mul_giv")
            self.emit(f"{prod} = fmul double {op_v}, {by_v}")
            for g_ref in stmt.giving_targets:
                self.emit_store_numeric(prod, g_ref)
                self._check_and_emit_size_error(prod, g_ref, stmt.on_size_error)
        else:
            for t_ref in stmt.by_targets:
                cur_v = self.emit_numeric_expr(t_ref)
                prod = self.new_reg("mul_by")
                self.emit(f"{prod} = fmul double {cur_v}, {op_v}")
                self.emit_store_numeric(prod, t_ref)
                self._check_and_emit_size_error(prod, t_ref, stmt.on_size_error)

    def _emit_divide(self, stmt: DivideStmt) -> None:
        op_v = self.emit_numeric_expr(stmt.operand)
        target_expr = stmt.targets[0] if stmt.targets else None
        t_v = self.emit_numeric_expr(target_expr)

        if stmt.mode == "BY":
            dividend, divisor = op_v, t_v
        else:
            dividend, divisor = t_v, op_v

        quot = self.new_reg("div_q")
        self.emit(f"{quot} = fdiv double {dividend}, {divisor}")

        if stmt.remainder is not None:
            # Quando há REMAINDER, o quociente inteiro é truncado e o resto é calculado
            q_i64 = self.new_reg("qi")
            self.emit(f"{q_i64} = fptosi double {quot} to i64")
            q_trunc = self.new_reg("qt")
            self.emit(f"{q_trunc} = sitofp i64 {q_i64} to double")
            prod = self.new_reg("qp")
            self.emit(f"{prod} = fmul double {q_trunc}, {divisor}")
            rem = self.new_reg("rem")
            self.emit(f"{rem} = fsub double {dividend}, {prod}")
            self.emit_store_numeric(rem, stmt.remainder)

        if stmt.giving_targets:
            for g_ref in stmt.giving_targets:
                self.emit_store_numeric(quot, g_ref)
        else:
            for tgt in stmt.targets:
                if isinstance(tgt, IdentifierRef):
                    self.emit_store_numeric(quot, tgt)

    def _emit_compute(self, stmt: ComputeStmt) -> None:
        val_reg = self.emit_numeric_expr(stmt.expression)
        for t_ref in stmt.targets:
            self.emit_store_numeric(val_reg, t_ref)
            self._check_and_emit_size_error(val_reg, t_ref, stmt.on_size_error)

    def _check_and_emit_size_error(
        self, val_reg: str, target_ref: IdentifierRef, on_size_error: list[StatementNode]
    ) -> None:
        if not on_size_error:
            return
        layout = self.resolve_field(target_ref)
        if layout is None or layout.category != "NUMERIC":
            return
        int_digits = max(1, layout.elem_size - layout.scale)
        limit = float(10 ** int_digits)

        abs_cmp1 = self.new_reg("sz_pos")
        abs_cmp2 = self.new_reg("sz_neg")
        is_ovf = self.new_reg("sz_ovf")
        self.emit(f"{abs_cmp1} = fcmp oge double {val_reg}, {limit:.8e}")
        self.emit(f"{abs_cmp2} = fcmp ole double {val_reg}, {-limit:.8e}")
        self.emit(f"{is_ovf} = or i1 {abs_cmp1}, {abs_cmp2}")

        err_lbl = self.new_label("size.err")
        cont_lbl = self.new_label("size.cont")
        self.emit_terminator(f"br i1 {is_ovf}, label %{err_lbl}, label %{cont_lbl}")

        self.emit_label(err_lbl)
        for s in on_size_error:
            self.emit_statement(s)
        if not self.block_terminated:
            self.emit_terminator(f"br label %{cont_lbl}")

        self.emit_label(cont_lbl)

    def _emit_evaluate(self, stmt: EvaluateStmt) -> None:
        end_lbl = self.new_label("eval.end")
        subj_is_alpha = self.is_alphanumeric_expr(stmt.subject)
        if subj_is_alpha:
            subj_ptr, subj_len = self.emit_string_slice(stmt.subject)
            subj_num = ""
        else:
            subj_num = self.emit_numeric_expr(stmt.subject)
            subj_ptr, subj_len = "", 0

        for br in stmt.branches:
            body_lbl = self.new_label("eval.when.body")
            next_lbl = self.new_label("eval.when.next")

            if isinstance(br.condition, str) and br.condition == "OTHER":
                self.emit_terminator(f"br label %{body_lbl}")
            elif isinstance(br.condition, ExprNode):
                if subj_is_alpha:
                    c_ptr, c_len = self.emit_string_slice(br.condition)
                    cmp_v = self.new_reg("ecmp")
                    self.emit(
                        f"{cmp_v} = call i32 @__cobol_cmp_alpha(ptr {subj_ptr}, i64 {subj_len}, ptr {c_ptr}, i64 {c_len})"
                    )
                    eq_reg = self.new_reg("eeq")
                    self.emit(f"{eq_reg} = icmp eq i32 {cmp_v}, 0")
                else:
                    c_num = self.emit_numeric_expr(br.condition)
                    eq_reg = self.new_reg("eeq")
                    self.emit(f"{eq_reg} = fcmp oeq double {subj_num}, {c_num}")
                self.emit_terminator(f"br i1 {eq_reg}, label %{body_lbl}, label %{next_lbl}")
            else:
                self.emit_terminator(f"br label %{next_lbl}")

            self.emit_label(body_lbl)
            for s in br.body:
                self.emit_statement(s)
            if not self.block_terminated:
                self.emit_terminator(f"br label %{end_lbl}")

            self.emit_label(next_lbl)

        self.emit_label(end_lbl)

    def _emit_search(self, stmt: SearchStmt) -> None:
        if stmt.table is None:
            return
        layout = self.resolve_field(stmt.table)
        if layout is None or not layout.dimensions:
            return

        max_occurs = layout.dimensions[0][0]
        # Descobre o índice associado à tabela (INDEXED BY)
        idx_sym: str | None = None
        for _, sym in self.index_globals.items():
            idx_sym = sym
            break
        if idx_sym is None:
            idx_sym = self.new_reg("search_idx")
            self.emit(f"{idx_sym} = alloca i64")
            self.emit(f"store i64 1, ptr {idx_sym}")

        cond_lbl = self.new_label("search.cond")
        body_lbl = self.new_label("search.body")
        at_end_lbl = self.new_label("search.at_end")
        done_lbl = self.new_label("search.done")

        self.emit_label(cond_lbl)
        cur_idx = self.new_reg("sidx")
        self.emit(f"{cur_idx} = load i64, ptr {idx_sym}")
        in_bounds = self.new_reg("sinb")
        self.emit(f"{in_bounds} = icmp sle i64 {cur_idx}, {max_occurs}")
        self.emit_terminator(f"br i1 {in_bounds}, label %{body_lbl}, label %{at_end_lbl}")

        self.emit_label(body_lbl)
        for br in stmt.branches:
            w_then = self.new_label("search.when.hit")
            w_next = self.new_label("search.when.next")
            c_reg = self.emit_condition(br.condition)
            self.emit_terminator(f"br i1 {c_reg}, label %{w_then}, label %{w_next}")

            self.emit_label(w_then)
            for s in br.body:
                self.emit_statement(s)
            if not self.block_terminated:
                self.emit_terminator(f"br label %{done_lbl}")

            self.emit_label(w_next)

        # Incrementa índice e repete
        cur_idx2 = self.new_reg("sidx_inc")
        self.emit(f"{cur_idx2} = load i64, ptr {idx_sym}")
        nxt_idx = self.new_reg("sidx_nxt")
        self.emit(f"{nxt_idx} = add i64 {cur_idx2}, 1")
        self.emit(f"store i64 {nxt_idx}, ptr {idx_sym}")
        self.emit_terminator(f"br label %{cond_lbl}")

        self.emit_label(at_end_lbl)
        for s in stmt.at_end:
            self.emit_statement(s)
        if not self.block_terminated:
            self.emit_terminator(f"br label %{done_lbl}")

        self.emit_label(done_lbl)

    def _emit_set(self, stmt: SetStmt) -> None:
        val_dbl = self.emit_numeric_expr(stmt.value)
        for t_ref in stmt.targets:
            if stmt.mode == "TO":
                self.emit_store_numeric(val_dbl, t_ref)
            elif stmt.mode == "UP BY":
                cur = self.emit_numeric_expr(t_ref)
                res = self.new_reg("set_up")
                self.emit(f"{res} = fadd double {cur}, {val_dbl}")
                self.emit_store_numeric(res, t_ref)
            elif stmt.mode == "DOWN BY":
                cur = self.emit_numeric_expr(t_ref)
                res = self.new_reg("set_dn")
                self.emit(f"{res} = fsub double {cur}, {val_dbl}")
                self.emit_store_numeric(res, t_ref)

    def _emit_inspect(self, stmt: InspectStmt) -> None:
        if stmt.target is None:
            return
        buf_ptr, buf_len, _ = self.emit_field_address_and_length(stmt.target)
        clauses = stmt.clauses

        # Extrai delimitadores opcionais BEFORE / AFTER INITIAL "<char>"
        before_ptr, before_len = "null", 0
        after_ptr, after_len = "null", 0
        for idx, tok in enumerate(clauses):
            if tok == "BEFORE" and idx + 1 < len(clauses):
                lit_tok = clauses[idx + 2] if (idx + 2 < len(clauses) and clauses[idx + 1] == "INITIAL") else clauses[idx + 1]
                clean = lit_tok.strip('"')
                before_ptr, before_len = self.intern_string(clean, null_terminate=False)
            elif tok == "AFTER" and idx + 1 < len(clauses):
                lit_tok = clauses[idx + 2] if (idx + 2 < len(clauses) and clauses[idx + 1] == "INITIAL") else clauses[idx + 1]
                clean = lit_tok.strip('"')
                after_ptr, after_len = self.intern_string(clean, null_terminate=False)

        if stmt.mode == "TALLYING" and clauses:
            counter_name = clauses[0]
            counter_ref = IdentifierRef(line=stmt.line, col=stmt.col, name=counter_name)
            cur_cnt = self.emit_numeric_expr(counter_ref)

            # Modos: FOR CHARACTERS (0), FOR ALL "X" (1), FOR LEADING "X" (2)
            tally_mode = 0
            pat_ptr, pat_len = "null", 0
            for idx, tok in enumerate(clauses):
                if tok == "CHARACTERS":
                    tally_mode = 0
                elif tok == "ALL" and idx + 1 < len(clauses):
                    tally_mode = 1
                    clean = clauses[idx + 1].strip('"')
                    pat_ptr, pat_len = self.intern_string(clean, null_terminate=False)
                elif tok == "LEADING" and idx + 1 < len(clauses):
                    tally_mode = 2
                    clean = clauses[idx + 1].strip('"')
                    pat_ptr, pat_len = self.intern_string(clean, null_terminate=False)

            added_i64 = self.new_reg("tally_n")
            self.emit(
                f"{added_i64} = call i64 @__cobol_inspect_tally("
                f"ptr {buf_ptr}, i64 {buf_len}, i32 {tally_mode}, "
                f"ptr {pat_ptr}, i64 {pat_len}, "
                f"ptr {after_ptr}, i64 {after_len}, "
                f"ptr {before_ptr}, i64 {before_len})"
            )
            added_dbl = self.new_reg("tally_d")
            self.emit(f"{added_dbl} = sitofp i64 {added_i64} to double")
            new_cnt = self.new_reg("tally_sum")
            self.emit(f"{new_cnt} = fadd double {cur_cnt}, {added_dbl}")
            self.emit_store_numeric(new_cnt, counter_ref)

        elif stmt.mode == "REPLACING":
            # Modos: CHARACTERS BY "X" (0), ALL "A" BY "B" (1), FIRST "A" BY "B" (2), LEADING "A" BY "B" (3)
            rep_mode = 1
            from_ptr, from_len = "null", 0
            to_ptr, to_len = "null", 0
            if "CHARACTERS" in clauses and "BY" in clauses:
                rep_mode = 0
                by_idx = clauses.index("BY")
                if by_idx + 1 < len(clauses):
                    clean_to = clauses[by_idx + 1].strip('"')
                    to_ptr, to_len = self.intern_string(clean_to, null_terminate=False)
            else:
                if "FIRST" in clauses:
                    rep_mode = 2
                elif "LEADING" in clauses:
                    rep_mode = 3
                else:
                    rep_mode = 1
                if "BY" in clauses:
                    by_idx = clauses.index("BY")
                    if by_idx - 1 >= 0:
                        clean_from = clauses[by_idx - 1].strip('"')
                        from_ptr, from_len = self.intern_string(clean_from, null_terminate=False)
                    if by_idx + 1 < len(clauses):
                        clean_to = clauses[by_idx + 1].strip('"')
                        to_ptr, to_len = self.intern_string(clean_to, null_terminate=False)

            self.emit(
                f"call void @__cobol_inspect_replace("
                f"ptr {buf_ptr}, i64 {buf_len}, i32 {rep_mode}, "
                f"ptr {from_ptr}, i64 {from_len}, ptr {to_ptr}, i64 {to_len}, "
                f"ptr {after_ptr}, i64 {after_len}, ptr {before_ptr}, i64 {before_len})"
            )

        elif stmt.mode == "CONVERTING" and "TO" in clauses:
            to_idx = clauses.index("TO")
            clean_from = clauses[0].strip('"') if to_idx > 0 else ""
            clean_to = clauses[to_idx + 1].strip('"') if to_idx + 1 < len(clauses) else ""
            from_ptr, from_len = self.intern_string(clean_from, null_terminate=False)
            to_ptr, to_len = self.intern_string(clean_to, null_terminate=False)
            self.emit(
                f"call void @__cobol_inspect_convert("
                f"ptr {buf_ptr}, i64 {buf_len}, ptr {from_ptr}, i64 {from_len}, ptr {to_ptr}, i64 {to_len})"
            )

    # --- Operações de Arquivo (OPEN, CLOSE, READ, WRITE, START) ---

    def _emit_open(self, stmt: OpenStmt) -> None:
        for grp in stmt.groups:
            c_mode = "r"
            if grp.mode == "OUTPUT":
                c_mode = "w+"
            elif grp.mode == "I-O":
                c_mode = "r+"
            elif grp.mode == "EXTEND":
                c_mode = "a+"
            mode_sym, _ = self.intern_string(c_mode, null_terminate=True)

            for fname in grp.files:
                f_info = self.files.get(fname)
                if f_info is None:
                    continue
                if f_info.assign_is_variable:
                    var_ref = IdentifierRef(line=stmt.line, col=stmt.col, name=f_info.assign_target)
                    fn_ptr, fn_len, _ = self.emit_field_address_and_length(var_ref)
                else:
                    fn_ptr, fn_len = self.intern_string(f_info.assign_target, null_terminate=False)

                st_code = self.new_reg("ost")
                self.emit(
                    f"{st_code} = call i64 @__cobol_file_open(ptr {f_info.handle_global}, ptr {fn_ptr}, i64 {fn_len}, ptr {mode_sym})"
                )
                self._update_file_status(f_info, st_code, stmt.line, stmt.col)

    def _emit_close(self, stmt: CloseStmt) -> None:
        for fname in stmt.files:
            f_info = self.files.get(fname)
            if f_info is None:
                continue
            self.emit(f"call void @__cobol_file_close(ptr {f_info.handle_global})")
            zero_st = self.new_reg("cst")
            self.emit(f"{zero_st} = add i64 0, 0")
            self._update_file_status(f_info, zero_st, stmt.line, stmt.col)

    def _emit_read(self, stmt: ReadStmt) -> None:
        f_info = self.files.get(stmt.file_name)
        if f_info is None or not f_info.records:
            return
        rec_name = f_info.records[0]
        rec_ref = IdentifierRef(line=stmt.line, col=stmt.col, name=rec_name)
        rec_ptr, rec_len, _ = self.emit_field_address_and_length(rec_ref)

        ok_reg = self.new_reg("rd_ok")
        self.emit(
            f"{ok_reg} = call i32 @__cobol_file_read(ptr {f_info.handle_global}, ptr {rec_ptr}, i64 {rec_len})"
        )

        if stmt.into is not None:
            into_ptr, into_len, _ = self.emit_field_address_and_length(stmt.into)
            self.emit(
                f"call void @__cobol_move_alpha(ptr {into_ptr}, i64 {into_len}, ptr {rec_ptr}, i64 {rec_len})"
            )

        self._emit_io_branches(
            ok_reg=ok_reg,
            on_fail=stmt.invalid_key or stmt.at_end,
            on_success=stmt.not_invalid_key or stmt.not_at_end,
        )

    def _emit_write(self, stmt: WriteStmt) -> None:
        fname = self.record_to_file.get(stmt.record_name, stmt.record_name)
        f_info = self.files.get(fname)
        if f_info is None:
            return

        rec_ref = IdentifierRef(line=stmt.line, col=stmt.col, name=stmt.record_name)
        rec_ptr, rec_len, _ = self.emit_field_address_and_length(rec_ref)

        if stmt.from_id is not None:
            from_ptr, from_len, _ = self.emit_field_address_and_length(stmt.from_id)
            self.emit(
                f"call void @__cobol_move_alpha(ptr {rec_ptr}, i64 {rec_len}, ptr {from_ptr}, i64 {from_len})"
            )

        ok_reg = self.new_reg("wr_ok")
        self.emit(
            f"{ok_reg} = call i32 @__cobol_file_write(ptr {f_info.handle_global}, ptr {rec_ptr}, i64 {rec_len})"
        )

        self._emit_io_branches(
            ok_reg=ok_reg,
            on_fail=stmt.invalid_key,
            on_success=stmt.not_invalid_key,
        )

    def _emit_start(self, stmt: StartStmt) -> None:
        f_info = self.files.get(stmt.file_name)
        if f_info is None:
            return
        fp_reg = self.new_reg("fp")
        self.emit(f"{fp_reg} = load ptr, ptr {f_info.handle_global}")
        is_null = self.new_reg("fp_null")
        self.emit(f"{is_null} = icmp eq ptr {fp_reg}, null")
        ok_reg = self.new_reg("st_ok")
        self.emit(f"{ok_reg} = select i1 {is_null}, i32 0, i32 1")
        self._emit_io_branches(
            ok_reg=ok_reg,
            on_fail=stmt.invalid_key,
            on_success=stmt.not_invalid_key,
        )

    def _emit_io_branches(
        self,
        ok_reg: str,
        on_fail: list[StatementNode],
        on_success: list[StatementNode],
    ) -> None:
        if not on_fail and not on_success:
            return
        cond = self.new_reg("io_cond")
        self.emit(f"{cond} = icmp ne i32 {ok_reg}, 0")
        ok_lbl = self.new_label("io.ok")
        fail_lbl = self.new_label("io.fail")
        end_lbl = self.new_label("io.end")

        self.emit_terminator(f"br i1 {cond}, label %{ok_lbl}, label %{fail_lbl}")

        self.emit_label(ok_lbl)
        for s in on_success:
            self.emit_statement(s)
        if not self.block_terminated:
            self.emit_terminator(f"br label %{end_lbl}")

        self.emit_label(fail_lbl)
        for s in on_fail:
            self.emit_statement(s)
        if not self.block_terminated:
            self.emit_terminator(f"br label %{end_lbl}")

        self.emit_label(end_lbl)

    def _update_file_status(self, f_info: FileRuntimeInfo, status_i64_reg: str, line: int, col: int) -> None:
        if not f_info.status_var:
            return
        st_dbl = self.new_reg("st_d")
        self.emit(f"{st_dbl} = sitofp i64 {status_i64_reg} to double")
        st_ref = IdentifierRef(line=line, col=col, name=f_info.status_var)
        self.emit_store_numeric(st_dbl, st_ref)

    # ==========================================================================
    # 5. GERAÇÃO DO MÓDULO COMPLETO EM TEXTO (.ll)
    # ==========================================================================

    def generate_module(self) -> str:
        self.build_layouts()

        # Pré-registra todos os parágrafos e seções da PROCEDURE DIVISION
        proc = self.program.procedure_division
        ordered_units: list[tuple[str, list[SentenceNode]]] = []

        if proc is not None:
            for para in proc.paragraphs:
                self._register_proc_unit(para.name, para.sentences, ordered_units)
            for sec in proc.sections:
                self._register_proc_unit(sec.name, sec.sentences, ordered_units)
                for para in sec.paragraphs:
                    self._register_proc_unit(para.name, para.sentences, ordered_units)

        # Emite o corpo de cada função de parágrafo/seção
        function_blocks: list[str] = []
        for unit_name, sentences in ordered_units:
            fn_sym = self.paragraph_functions[unit_name]
            self.current_lines = [f"define internal void {fn_sym}() {{", "entry:"]
            self.block_terminated = False
            for sent in sentences:
                for st in sent.statements:
                    self.emit_statement(st)
            if not self.block_terminated:
                self.emit_terminator("ret void")
            self.current_lines.append("}")
            function_blocks.append("\n".join(self.current_lines))

        # Emite a função @main()
        self.current_lines = ["define i32 @main() {", "entry:"]
        self.block_terminated = False
        if proc is not None:
            for sent in proc.sentences:
                for st in sent.statements:
                    self.emit_statement(st)
            # Em COBOL, a execução inicia no primeiro parágrafo/seção e flui sequencialmente até STOP RUN
            for unit_name, _ in ordered_units:
                fn_sym = self.paragraph_functions[unit_name]
                self.emit(f"call void {fn_sym}()")
        if not self.block_terminated:
            self.emit_terminator("ret i32 0")
        self.current_lines.append("}")
        main_block = "\n".join(self.current_lines)

        # Monta o cabeçalho, globais, strings, runtime embutido e funções do programa
        prog_id = (
            self.program.identification_division.program_id
            if self.program.identification_division and self.program.identification_division.program_id
            else "COBOL-PROGRAM"
        )

        out: list[str] = [
            f"; ModuleID = '{prog_id}'",
            f"source_filename = \"{prog_id}.cob\"",
            "",
            "; --- Declarações Externas da libc e Intrínsecos LLVM ---",
            "declare i32 @printf(ptr, ...)",
            "declare i32 @snprintf(ptr, i64, ptr, ...)",
            "declare i32 @putchar(i32)",
            "declare void @exit(i32)",
            "declare ptr @fopen(ptr, ptr)",
            "declare i32 @fclose(ptr)",
            "declare i64 @fread(ptr, i64, i64, ptr)",
            "declare i64 @fwrite(ptr, i64, i64, ptr)",
            "declare i32 @fgetc(ptr)",
            "declare i32 @getchar()",
            "declare double @strtod(ptr, ptr)",
            "declare double @llvm.pow.f64(double, double)",
            "declare double @llvm.fabs.f64(double)",
            "declare double @llvm.round.f64(double)",
            "declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)",
            "declare void @llvm.memmove.p0.p0.i64(ptr, ptr, i64, i1)",
            "declare void @llvm.memset.p0.i64(ptr, i8, i64, i1)",
            "",
            "; --- Buffers Globais da DATA DIVISION ---",
        ]

        for rb in self.root_buffers:
            esc = llvm_escape_bytes(bytes(rb.initial_bytes))
            out.append(f'{rb.global_name} = internal global [{rb.size} x i8] c"{esc}"')

        for _, idx_sym in self.index_globals.items():
            out.append(f"{idx_sym} = internal global i64 1")

        for f_info in self.files.values():
            out.append(f"{f_info.handle_global} = internal global ptr null")

        out.append("")
        out.append("; --- Pool de Literais Alfanuméricos e Formatos ---")
        for raw_bytes, (sym, length) in self.string_pool.items():
            esc = llvm_escape_bytes(raw_bytes)
            out.append(f'{sym} = private unnamed_addr constant [{length} x i8] c"{esc}"')

        out.append("")
        out.append(_LLVM_RUNTIME_HELPERS)
        out.append("")
        out.append("; --- Parágrafos e Seções da PROCEDURE DIVISION ---")
        out.extend(function_blocks)
        out.append("")
        out.append("; --- Ponto de Entrada Principal ---")
        out.append(main_block)
        out.append("")

        return "\n".join(out)

    def _register_proc_unit(
        self,
        name: str,
        sentences: list[SentenceNode],
        ordered_units: list[tuple[str, list[SentenceNode]]],
    ) -> None:
        if name in self.paragraph_functions:
            return
        fn_sym = f"@para_{mangle_name(name)}"
        self.paragraph_Order.append(name)
        self.paragraph_functions[name] = fn_sym
        ordered_units.append((name, sentences))


# ==============================================================================
# ROTINAS DE RUNTIME COBOL EM REPRESENTAÇÃO INTERMEDIÁRIA LLVM (.ll)
# ==============================================================================

_LLVM_RUNTIME_HELPERS = r"""; --- Biblioteca de Runtime COBOL em LLVM IR ---
@.fmt.zero_int = private unnamed_addr constant [6 x i8] c"%0*ld\00"
@.fmt.edit_dec = private unnamed_addr constant [8 x i8] c"%*.*f\00\00\00"

define internal double @__cobol_read_num(ptr %buf, i64 %len, i64 %scale) {
entry:
  %tmp = alloca [128 x i8]
  %clamped = icmp ugt i64 %len, 120
  %n = select i1 %clamped, i64 120, i64 %len
  call void @llvm.memcpy.p0.p0.i64(ptr %tmp, ptr %buf, i64 %n, i1 false)
  %endp = getelementptr inbounds i8, ptr %tmp, i64 %n
  store i8 0, ptr %endp
  %raw = call double @strtod(ptr %tmp, ptr null)
  ; Se houver '.' explícito no buffer, strtod já considerou as casas decimais
  br label %check_dot

check_dot:
  %i = phi i64 [ 0, %entry ], [ %i.next, %loop_body ]
  %done = icmp uge i64 %i, %n
  br i1 %done, label %apply_scale, label %loop_body

loop_body:
  %cp = getelementptr inbounds i8, ptr %tmp, i64 %i
  %ch = load i8, ptr %cp
  %is_dot = icmp eq i8 %ch, 46
  %i.next = add i64 %i, 1
  br i1 %is_dot, label %ret_raw, label %check_dot

apply_scale:
  %has_scale = icmp sgt i64 %scale, 0
  br i1 %has_scale, label %do_div, label %ret_raw

do_div:
  %sc_dbl = sitofp i64 %scale to double
  %factor = call double @llvm.pow.f64(double 1.000000e+01, double %sc_dbl)
  %scaled = fdiv double %raw, %factor
  ret double %scaled

ret_raw:
  ret double %raw
}

define internal void @__cobol_write_num(double %val, ptr %buf, i64 %len, i64 %scale, i32 %is_edited) {
entry:
  %tmp = alloca [128 x i8]
  %ed_cond = icmp ne i32 %is_edited, 0
  br i1 %ed_cond, label %write_edited, label %write_zoned

write_edited:
  %w_i32 = trunc i64 %len to i32
  %s_i32 = trunc i64 %scale to i32
  %nw1 = call i32 (ptr, i64, ptr, ...) @snprintf(ptr %tmp, i64 128, ptr @.fmt.edit_dec, i32 %w_i32, i32 %s_i32, double %val)
  call void @llvm.memcpy.p0.p0.i64(ptr %buf, ptr %tmp, i64 %len, i1 false)
  ret void

write_zoned:
  %sc_dbl = sitofp i64 %scale to double
  %factor = call double @llvm.pow.f64(double 1.000000e+01, double %sc_dbl)
  %abs_v = call double @llvm.fabs.f64(double %val)
  %mul_v = fmul double %abs_v, %factor
  %rnd_v = call double @llvm.round.f64(double %mul_v)
  %int_v = fptosi double %rnd_v to i64
  %w_z = trunc i64 %len to i32
  %nw2 = call i32 (ptr, i64, ptr, ...) @snprintf(ptr %tmp, i64 128, ptr @.fmt.zero_int, i32 %w_z, i64 %int_v)
  %nw_i64 = sext i32 %nw2 to i64
  %over = icmp sgt i64 %nw_i64, %len
  %ofs = sub i64 %nw_i64, %len
  %src_ofs = select i1 %over, i64 %ofs, i64 0
  %src_ptr = getelementptr inbounds i8, ptr %tmp, i64 %src_ofs
  call void @llvm.memcpy.p0.p0.i64(ptr %buf, ptr %src_ptr, i64 %len, i1 false)
  ret void
}

define internal void @__cobol_move_alpha(ptr %dst, i64 %dst_len, ptr %src, i64 %src_len) {
entry:
  %cmp = icmp ult i64 %dst_len, %src_len
  %n = select i1 %cmp, i64 %dst_len, i64 %src_len
  call void @llvm.memmove.p0.p0.i64(ptr %dst, ptr %src, i64 %n, i1 false)
  %need_pad = icmp ugt i64 %dst_len, %n
  br i1 %need_pad, label %pad, label %done

pad:
  %rem = sub i64 %dst_len, %n
  %tail = getelementptr inbounds i8, ptr %dst, i64 %n
  call void @llvm.memset.p0.i64(ptr %tail, i8 32, i64 %rem, i1 false)
  br label %done

done:
  ret void
}

define internal void @__cobol_upper_inplace(ptr %buf, i64 %len) {
entry:
  br label %loop

loop:
  %i = phi i64 [ 0, %entry ], [ %i.next, %next ]
  %done = icmp uge i64 %i, %len
  br i1 %done, label %exit, label %body

body:
  %p = getelementptr inbounds i8, ptr %buf, i64 %i
  %c = load i8, ptr %p
  %ge_a = icmp sge i8 %c, 97
  %le_z = icmp sle i8 %c, 122
  %is_low = and i1 %ge_a, %le_z
  %up = sub i8 %c, 32
  %res = select i1 %is_low, i8 %up, i8 %c
  store i8 %res, ptr %p
  br label %next

next:
  %i.next = add i64 %i, 1
  br label %loop

exit:
  ret void
}

define internal i32 @__cobol_cmp_alpha(ptr %a, i64 %alen, ptr %b, i64 %blen) {
entry:
  %gt = icmp ugt i64 %alen, %blen
  %maxlen = select i1 %gt, i64 %alen, i64 %blen
  br label %loop

loop:
  %i = phi i64 [ 0, %entry ], [ %i.next, %cont ]
  %done = icmp uge i64 %i, %maxlen
  br i1 %done, label %ret_eq, label %body

body:
  %in_a = icmp ult i64 %i, %alen
  br i1 %in_a, label %load_a, label %space_a

load_a:
  %pa = getelementptr inbounds i8, ptr %a, i64 %i
  %ca_ld = load i8, ptr %pa
  br label %after_a

space_a:
  br label %after_a

after_a:
  %ca = phi i8 [ %ca_ld, %load_a ], [ 32, %space_a ]
  %in_b = icmp ult i64 %i, %blen
  br i1 %in_b, label %load_b, label %space_b

load_b:
  %pb = getelementptr inbounds i8, ptr %b, i64 %i
  %cb_ld = load i8, ptr %pb
  br label %after_b

space_b:
  br label %after_b

after_b:
  %cb = phi i8 [ %cb_ld, %load_b ], [ 32, %space_b ]
  %eq = icmp eq i8 %ca, %cb
  br i1 %eq, label %cont, label %diff

cont:
  %i.next = add i64 %i, 1
  br label %loop

diff:
  %ca32 = zext i8 %ca to i32
  %cb32 = zext i8 %cb to i32
  %sub = sub i32 %ca32, %cb32
  ret i32 %sub

ret_eq:
  ret i32 0
}

define internal void @__cobol_accept(ptr %buf, i64 %len, i64 %scale, i32 %is_num, i32 %is_edited) {
entry:
  %tmp = alloca [256 x i8]
  call void @llvm.memset.p0.i64(ptr %tmp, i8 0, i64 256, i1 false)
  br label %read_loop

read_loop:
  %i = phi i64 [ 0, %entry ], [ %i, %check_cr ], [ %i.next, %store_ch ]
  %ch = call i32 @getchar()
  %is_eof = icmp slt i32 %ch, 0
  %is_nl = icmp eq i32 %ch, 10
  %stop = or i1 %is_eof, %is_nl
  br i1 %stop, label %finish, label %check_cr

check_cr:
  %is_cr = icmp eq i32 %ch, 13
  %full = icmp uge i64 %i, 250
  %skip = or i1 %is_cr, %full
  br i1 %skip, label %read_loop, label %store_ch

store_ch:
  %p = getelementptr inbounds i8, ptr %tmp, i64 %i
  %b = trunc i32 %ch to i8
  store i8 %b, ptr %p
  %i.next = add i64 %i, 1
  br label %read_loop

finish:
  %num_cond = icmp ne i32 %is_num, 0
  br i1 %num_cond, label %parse_num, label %copy_alpha

parse_num:
  %v = call double @strtod(ptr %tmp, ptr null)
  call void @__cobol_write_num(double %v, ptr %buf, i64 %len, i64 %scale, i32 %is_edited)
  ret void

copy_alpha:
  call void @__cobol_move_alpha(ptr %buf, i64 %len, ptr %tmp, i64 %i)
  ret void
}

define internal i64 @__cobol_find_bound(ptr %buf, i64 %len, ptr %pat, i64 %plen, i32 %is_after) {
entry:
  %no_pat = icmp eq ptr %pat, null
  %zero_p = icmp eq i64 %plen, 0
  %skip = or i1 %no_pat, %zero_p
  br i1 %skip, label %default_ret, label %loop

default_ret:
  %def_v = select i1 %no_pat, i64 0, i64 0
  %is_af = icmp ne i32 %is_after, 0
  %ret_d = select i1 %is_af, i64 0, i64 %len
  ret i64 %ret_d

loop:
  %i = phi i64 [ 0, %entry ], [ %i.next, %next ]
  %rem = sub i64 %len, %i
  %fits = icmp uge i64 %rem, %plen
  br i1 %fits, label %check, label %not_found

check:
  %subp = getelementptr inbounds i8, ptr %buf, i64 %i
  %cmp = call i32 @__cobol_cmp_alpha(ptr %subp, i64 %plen, ptr %pat, i64 %plen)
  %hit = icmp eq i32 %cmp, 0
  br i1 %hit, label %found, label %next

next:
  %i.next = add i64 %i, 1
  br label %loop

found:
  %is_af2 = icmp ne i32 %is_after, 0
  %after_pos = add i64 %i, %plen
  %res = select i1 %is_af2, i64 %after_pos, i64 %i
  ret i64 %res

not_found:
  %is_af3 = icmp ne i32 %is_after, 0
  %nf = select i1 %is_af3, i64 %len, i64 %len
  ret i64 %nf
}

define internal i64 @__cobol_inspect_tally(
    ptr %buf, i64 %len, i32 %mode,
    ptr %pat, i64 %plen,
    ptr %after_p, i64 %after_l,
    ptr %before_p, i64 %before_l) {
entry:
  %start = call i64 @__cobol_find_bound(ptr %buf, i64 %len, ptr %after_p, i64 %after_l, i32 1)
  %end = call i64 @__cobol_find_bound(ptr %buf, i64 %len, ptr %before_p, i64 %before_l, i32 0)
  %is_chars = icmp eq i32 %mode, 0
  br i1 %is_chars, label %tally_chars, label %loop

tally_chars:
  %valid_range = icmp sgt i64 %end, %start
  %diff = sub i64 %end, %start
  %c_res = select i1 %valid_range, i64 %diff, i64 0
  ret i64 %c_res

loop:
  %pos = phi i64 [ %start, %entry ], [ %pos.next, %step ]
  %cnt = phi i64 [ 0, %entry ], [ %cnt.next, %step ]
  %avail = sub i64 %end, %pos
  %can_match = icmp sge i64 %avail, %plen
  %p_pos = icmp sgt i64 %plen, 0
  %ok = and i1 %can_match, %p_pos
  br i1 %ok, label %test_pat, label %done

test_pat:
  %cur_p = getelementptr inbounds i8, ptr %buf, i64 %pos
  %cmp = call i32 @__cobol_cmp_alpha(ptr %cur_p, i64 %plen, ptr %pat, i64 %plen)
  %matched = icmp eq i32 %cmp, 0
  br i1 %matched, label %on_match, label %on_miss

on_match:
  %c_inc = add i64 %cnt, 1
  %p_adv = add i64 %pos, %plen
  br label %step

on_miss:
  %is_leading = icmp eq i32 %mode, 2
  br i1 %is_leading, label %done, label %miss_adv

miss_adv:
  %p_one = add i64 %pos, 1
  br label %step

step:
  %pos.next = phi i64 [ %p_adv, %on_match ], [ %p_one, %miss_adv ]
  %cnt.next = phi i64 [ %c_inc, %on_match ], [ %cnt, %miss_adv ]
  br label %loop

done:
  ret i64 %cnt
}

define internal void @__cobol_inspect_replace(
    ptr %buf, i64 %len, i32 %mode,
    ptr %from_p, i64 %from_l, ptr %to_p, i64 %to_l,
    ptr %after_p, i64 %after_l, ptr %before_p, i64 %before_l) {
entry:
  %start = call i64 @__cobol_find_bound(ptr %buf, i64 %len, ptr %after_p, i64 %after_l, i32 1)
  %end = call i64 @__cobol_find_bound(ptr %buf, i64 %len, ptr %before_p, i64 %before_l, i32 0)
  %is_chars = icmp eq i32 %mode, 0
  br i1 %is_chars, label %rep_chars_loop, label %rep_pat_loop

rep_chars_loop:
  %ci = phi i64 [ %start, %entry ], [ %ci.next, %rep_chars_body ]
  %cdone = icmp sge i64 %ci, %end
  br i1 %cdone, label %exit, label %rep_chars_body

rep_chars_body:
  %ch = load i8, ptr %to_p
  %dst_c = getelementptr inbounds i8, ptr %buf, i64 %ci
  store i8 %ch, ptr %dst_c
  %ci.next = add i64 %ci, 1
  br label %rep_chars_loop

rep_pat_loop:
  %pos = phi i64 [ %start, %entry ], [ %pos.next, %rep_step ]
  %avail = sub i64 %end, %pos
  %can_m = icmp sge i64 %avail, %from_l
  %pos_l = icmp sgt i64 %from_l, 0
  %ok = and i1 %can_m, %pos_l
  br i1 %ok, label %check_m, label %exit

check_m:
  %cur_p = getelementptr inbounds i8, ptr %buf, i64 %pos
  %cmp = call i32 @__cobol_cmp_alpha(ptr %cur_p, i64 %from_l, ptr %from_p, i64 %from_l)
  %hit = icmp eq i32 %cmp, 0
  br i1 %hit, label %do_rep, label %miss_rep

do_rep:
  call void @llvm.memcpy.p0.p0.i64(ptr %cur_p, ptr %to_p, i64 %from_l, i1 false)
  %is_first = icmp eq i32 %mode, 2
  %p_jump = add i64 %pos, %from_l
  br i1 %is_first, label %exit, label %rep_step

miss_rep:
  %is_lead = icmp eq i32 %mode, 3
  %p_step1 = add i64 %pos, 1
  br i1 %is_lead, label %exit, label %rep_step

rep_step:
  %pos.next = phi i64 [ %p_jump, %do_rep ], [ %p_step1, %miss_rep ]
  br label %rep_pat_loop

exit:
  ret void
}

define internal void @__cobol_inspect_convert(
    ptr %buf, i64 %len, ptr %from_p, i64 %from_l, ptr %to_p, i64 %to_l) {
entry:
  br label %outer

outer:
  %i = phi i64 [ 0, %entry ], [ %i.next, %outer_next ]
  %done = icmp uge i64 %i, %len
  br i1 %done, label %exit, label %inner_init

inner_init:
  %bp = getelementptr inbounds i8, ptr %buf, i64 %i
  %bc = load i8, ptr %bp
  br label %inner

inner:
  %j = phi i64 [ 0, %inner_init ], [ %j.next, %inner_cont ]
  %jdone = icmp uge i64 %j, %from_l
  br i1 %jdone, label %outer_next, label %inner_check

inner_check:
  %fp = getelementptr inbounds i8, ptr %from_p, i64 %j
  %fc = load i8, ptr %fp
  %eq = icmp eq i8 %bc, %fc
  br i1 %eq, label %convert_hit, label %inner_cont

convert_hit:
  %tp = getelementptr inbounds i8, ptr %to_p, i64 %j
  %tc = load i8, ptr %tp
  store i8 %tc, ptr %bp
  br label %outer_next

inner_cont:
  %j.next = add i64 %j, 1
  br label %inner

outer_next:
  %i.next = add i64 %i, 1
  br label %outer

exit:
  ret void
}

define internal i64 @__cobol_file_open(ptr %handle_slot, ptr %name_buf, i64 %name_len, ptr %mode) {
entry:
  %old = load ptr, ptr %handle_slot
  %has_old = icmp ne ptr %old, null
  br i1 %has_old, label %close_prev, label %prepare_name

close_prev:
  %c_res = call i32 @fclose(ptr %old)
  store ptr null, ptr %handle_slot
  br label %prepare_name

prepare_name:
  %tmp = alloca [256 x i8]
  call void @llvm.memset.p0.i64(ptr %tmp, i8 0, i64 256, i1 false)
  %clamp = icmp ugt i64 %name_len, 250
  %n = select i1 %clamp, i64 250, i64 %name_len
  call void @llvm.memcpy.p0.p0.i64(ptr %tmp, ptr %name_buf, i64 %n, i1 false)
  br label %trim_loop

trim_loop:
  %k = phi i64 [ %n, %prepare_name ], [ %k.prev, %trim_body ]
  %gt0 = icmp ugt i64 %k, 0
  br i1 %gt0, label %check_sp, label %do_open

check_sp:
  %k.prev = sub i64 %k, 1
  %kp = getelementptr inbounds i8, ptr %tmp, i64 %k.prev
  %kc = load i8, ptr %kp
  %is_sp = icmp eq i8 %kc, 32
  br i1 %is_sp, label %trim_body, label %do_open

trim_body:
  store i8 0, ptr %kp
  br label %trim_loop

do_open:
  %fp = call ptr @fopen(ptr %tmp, ptr %mode)
  store ptr %fp, ptr %handle_slot
  %ok = icmp ne ptr %fp, null
  %status = select i1 %ok, i64 0, i64 35
  ret i64 %status
}

define internal void @__cobol_file_close(ptr %handle_slot) {
entry:
  %fp = load ptr, ptr %handle_slot
  %ok = icmp ne ptr %fp, null
  br i1 %ok, label %do_close, label %exit

do_close:
  %r = call i32 @fclose(ptr %fp)
  store ptr null, ptr %handle_slot
  br label %exit

exit:
  ret void
}

define internal i32 @__cobol_file_read(ptr %handle_slot, ptr %rec_buf, i64 %rec_len) {
entry:
  %fp = load ptr, ptr %handle_slot
  %is_null = icmp eq ptr %fp, null
  br i1 %is_null, label %fail, label %do_read

do_read:
  call void @llvm.memset.p0.i64(ptr %rec_buf, i8 32, i64 %rec_len, i1 false)
  %nr = call i64 @fread(ptr %rec_buf, i64 1, i64 %rec_len, ptr %fp)
  %got = icmp ugt i64 %nr, 0
  br i1 %got, label %consume_nl, label %fail

consume_nl:
  %ch = call i32 @fgetc(ptr %fp)
  ret i32 1

fail:
  ret i32 0
}

define internal i32 @__cobol_file_write(ptr %handle_slot, ptr %rec_buf, i64 %rec_len) {
entry:
  %fp = load ptr, ptr %handle_slot
  %is_null = icmp eq ptr %fp, null
  br i1 %is_null, label %fail, label %do_write

do_write:
  %nw = call i64 @fwrite(ptr %rec_buf, i64 1, i64 %rec_len, ptr %fp)
  %nl = alloca i8
  store i8 10, ptr %nl
  %nlnw = call i64 @fwrite(ptr %nl, i64 1, i64 1, ptr %fp)
  %ok = icmp eq i64 %nw, %rec_len
  %res = select i1 %ok, i32 1, i32 0
  ret i32 %res

fail:
  ret i32 0
}
"""


# ==============================================================================
# INTERFACE PÚBLICA E PONTO DE ENTRADA (CLI)
# ==============================================================================


def generate_llvm_ir(program: ProgramNode) -> str:
    generator = LLVMIRGenerator(program)
    return generator.generate_module()


def try_compile_llvm_ir(ll_path: str, bin_path: str) -> tuple[bool, str]:
    """Tenta compilar o arquivo .ll para binário nativo caso clang ou llc+gcc estejam instalados."""
    clang_bin = shutil.which("clang")
    if clang_bin:
        proc = subprocess.run(
            [clang_bin, ll_path, "-lm", "-o", bin_path],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return True, f"Executável nativo compilado via clang: '{bin_path}'"
        return False, f"Erro ao compilar com clang:\n{proc.stderr}"

    llc_bin = shutil.which("llc")
    gcc_bin = shutil.which("gcc")
    if llc_bin and gcc_bin:
        obj_path = ll_path + ".o"
        p1 = subprocess.run([llc_bin, "-filetype=obj", ll_path, "-o", obj_path], capture_output=True, text=True)
        if p1.returncode != 0:
            return False, f"Erro ao montar com llc:\n{p1.stderr}"
        p2 = subprocess.run([gcc_bin, obj_path, "-lm", "-no-pie", "-o", bin_path], capture_output=True, text=True)
        if os.path.exists(obj_path):
            os.remove(obj_path)
        if p2.returncode == 0:
            return True, f"Executável nativo compilado via llc + gcc: '{bin_path}'"
        return False, f"Erro ao linkeditar com gcc:\n{p2.stderr}"

    return False, "Binários 'clang' ou 'llc' não encontrados no PATH; apenas o arquivo .ll foi gerado."


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python llvm_codegen.py <arquivo_fonte.cob> [-o saida.ll] [--compile] [--debug]")
        sys.exit(1)

    caminho_arquivo = sys.argv[1]
    modo_depuracao = "--debug" in sys.argv[2:]
    compilar_binario = "--compile" in sys.argv[2:]

    caminho_saida_ll = os.path.splitext(caminho_arquivo)[0] + ".ll"
    if "-o" in sys.argv[2:]:
        idx_o = sys.argv.index("-o")
        if idx_o + 1 < len(sys.argv):
            caminho_saida_ll = sys.argv[idx_o + 1]

    try:
        with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
            linhas_do_fonte = arquivo.readlines()

        # 1. Análise Léxica e Expansão de Copybooks
        stream_de_tokens, erros = make_token_stream(linhas_do_fonte, modo_depuracao)
        diretorio_base = os.path.dirname(os.path.abspath(caminho_arquivo))
        stream_de_tokens = expand_copybooks(
            stream_de_tokens, erros, diretorio_base, modo_depuracao
        )

        # 2. Análise Sintática
        arvore_sintatica, erros = parse(stream_de_tokens, erros)

        # 3. Análise Semântica
        valido, erros = analyze(arvore_sintatica, erros)

        if not valido:
            print("[FALHA] A geração de código LLVM IR foi abortada devido a erros de compilação:")
            print()
            print(f"{'SEV':<5} | {'LINHA':<7} | {'COL':<5} | {'MENSAGEM'}")
            print("-" * 70)
            for erro in erros:
                print(f"{erro.severity:<5} | {erro.line:<7} | {erro.col:<5} | {erro.message}")
            sys.exit(1)

        # 4. Geração de Representação Intermediária LLVM (.ll)
        codigo_llvm = generate_llvm_ir(arvore_sintatica)
        with open(caminho_saida_ll, "w", encoding="utf-8") as out_file:
            out_file.write(codigo_llvm)

        print(f"[OK] Representação Intermediária LLVM gerada com sucesso em: '{caminho_saida_ll}'")

        if compilar_binario or shutil.which("clang") or shutil.which("llc"):
            caminho_bin = os.path.splitext(caminho_saida_ll)[0]
            ok_bin, msg_bin = try_compile_llvm_ir(caminho_saida_ll, caminho_bin)
            print(f"[INFO] {msg_bin}")

    except FileNotFoundError:
        print(f"Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
        sys.exit(1)
