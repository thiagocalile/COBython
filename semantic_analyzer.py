# Integrantes do grupo 9:
#   Thiago Costa    - Nº USP 14609529
#   Oliver Kuramae  - Nº USP 14601326
#   Theo Brito      - Nº USP 13688367

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

from lexer import (
    CompileError,
    expand_copybooks,
    make_token_stream,
    number_regex,
    user_identifier_regex,
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
    FileDescriptionNode,
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
    PerformStmt,
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

# ==============================================================================
# ESTRUTURAS DA TABELA DE SÍMBOLOS
# ==============================================================================


@dataclass
class SymbolEntry:
    name: str
    level: int
    category: str  # ALPHABETIC, NUMERIC, ALPHANUMERIC, GROUP, CONDITION, INDEX
    pic: str | None
    ancestors: tuple[str, ...]  # Grupos ancestrais do nível mais externo (01) ao pai imediato
    scope_parent: str           # Nome do grupo pai imediato ou "<GLOBAL>"
    line: int
    col: int


def classify_pic(pic: str) -> str:
    """Classifica a máscara PICTURE em ALPHABETIC (PIC A), NUMERIC (PIC 9) ou ALPHANUMERIC (PIC X)."""
    cleaned = re.sub(r"\([0-9]+\)", "", pic.upper())
    has_x = "X" in cleaned
    has_a = "A" in cleaned
    has_9 = any(c in cleaned for c in "9Z*")

    if has_x or (has_a and has_9):
        return "ALPHANUMERIC"
    if has_a:
        return "ALPHABETIC"
    if has_9:
        return "NUMERIC"
    return "ALPHANUMERIC"


def compute_pic_byte_size(pic: str | None) -> int:
    """Calcula o tamanho físico em bytes de uma máscara PICTURE."""
    if not pic:
        return 0
    expanded = re.sub(
        r"([9XASVZPB0/,.\-+*$])\(([0-9]+)\)",
        lambda m: m.group(1) * int(m.group(2)),
        pic.upper(),
    )
    physical = [c for c in expanded if c not in ("V", "S", "P")]
    return max(1, len(physical))


def classify_data_item(item: DataItemNode) -> str:
    """Determina a categoria semântica de um DataItemNode."""
    if item.level == 88:
        return "CONDITION"
    non_88_children = [c for c in item.children if c.level != 88]
    if non_88_children and item.pic is None:
        return "GROUP"
    if item.pic is not None:
        return classify_pic(item.pic)
    return "GROUP"


# ==============================================================================
# ANALISADOR SEMÂNTICO (SEÇÃO 4 DA ESPECIFICAÇÃO TEXTUAL)
# ==============================================================================


class SemanticAnalyzer:
    def __init__(self, program: ProgramNode, errors: list[CompileError] | None = None):
        self.program: ProgramNode = program
        self.errors: list[CompileError] = list(errors) if errors is not None else []

        # Tabela de símbolos de variáveis e grupos: nome -> lista de declarações
        self.symbols_by_name: dict[str, list[SymbolEntry]] = {}
        # Controle de unicidade no mesmo escopo: (scope_parent, name) -> SymbolEntry
        self.scoped_symbols: dict[tuple[str, str], SymbolEntry] = {}
        # Rastreamento de símbolos da WORKING-STORAGE e referências para diagnósticos nível I (Seção 6)
        self.working_storage_entries: list[SymbolEntry] = []
        self.referenced_names: set[str] = set()

        # Tabelas de arquivos e registros (Regra 4)
        self.select_files: dict[str, SelectNode] = {}
        self.fd_files: dict[str, FileDescriptionNode] = {}
        self.record_to_fd: dict[str, str] = {}

        # Tabela de destinos de controle da PROCEDURE DIVISION (Regra 3)
        self.procedures: set[str] = set()

    def add_error(self, line: int, col: int, message: str, severity: str = "E") -> None:
        self.errors.append(CompileError(severity, line, col, message))

    def analyze(self) -> tuple[bool, list[CompileError]]:
        # 1. Construir tabelas de símbolos e validar declarações (Regras 2, 3 e 4)
        self._collect_environment_files()
        self._collect_data_division()
        self._check_file_declarations_consistency()
        self._validate_select_variable_refs()
        self._collect_procedure_targets()

        # 2. Validar instruções, expressões e comandos da PROCEDURE DIVISION (Regras 1, 2, 3 e 4)
        self._analyze_procedure_division()

        # 3. Emitir diagnósticos informativos (nível I da Seção 6) para variáveis não utilizadas
        self._check_unused_variables()

        is_valid = not any(err.severity in ("E", "S") for err in self.errors)
        return is_valid, self.errors

    # ==========================================================================
    # COLETA E VALIDAÇÃO DE DECLARAÇÕES (ENVIRONMENT, DATA, PROCEDURE)
    # ==========================================================================

    def _collect_environment_files(self) -> None:
        env = self.program.environment_division
        if env is None or env.input_output_section is None:
            return

        for sel in env.input_output_section.file_control:
            if sel.file_name in ("<DESCONHECIDO>", "<ANONIMO>"):
                continue
            if sel.file_name in self.select_files:
                self.add_error(
                    sel.line,
                    sel.col,
                    f"Arquivo '{sel.file_name}' declarado mais de uma vez no FILE-CONTROL",
                )
            else:
                self.select_files[sel.file_name] = sel

    def _collect_data_division(self) -> None:
        data_div = self.program.data_division
        if data_div is None:
            return

        # 1. FILE SECTION (descritores FD e seus registros 01)
        for fd in data_div.file_section:
            if fd.file_name not in ("<DESCONHECIDO>", "<ANONIMO>"):
                if fd.file_name in self.fd_files:
                    self.add_error(
                        fd.line,
                        fd.col,
                        f"Descritor FD para o arquivo '{fd.file_name}' declarado mais de uma vez na DATA DIVISION",
                    )
                else:
                    self.fd_files[fd.file_name] = fd

            for rec in fd.records:
                if rec.name not in ("FILLER", "<ANONIMO>", "<DESCONHECIDO>"):
                    self.record_to_fd[rec.name] = fd.file_name
                self._register_data_item(rec, ancestors=())

        # 2. WORKING-STORAGE SECTION
        for item in data_div.working_storage:
            self._register_data_item(item, ancestors=(), is_working_storage=True)

    def _register_data_item(
        self, item: DataItemNode, ancestors: tuple[str, ...], is_working_storage: bool = False
    ) -> None:
        scope_parent = ancestors[-1] if ancestors else "<GLOBAL>"
        category = classify_data_item(item)

        if item.name not in ("FILLER", "<ANONIMO>", "<DESCONHECIDO>"):
            entry = SymbolEntry(
                name=item.name,
                level=item.level,
                category=category,
                pic=item.pic,
                ancestors=ancestors,
                scope_parent=scope_parent,
                line=item.line,
                col=item.col,
            )

            # Regra 2: Unicidade de Identificadores dentro do mesmo escopo
            scope_key = (scope_parent, item.name)
            if scope_key in self.scoped_symbols:
                prev = self.scoped_symbols[scope_key]
                escopo_desc = (
                    "escopo global"
                    if scope_parent == "<GLOBAL>"
                    else f"grupo '{scope_parent}'"
                )
                self.add_error(
                    item.line,
                    item.col,
                    f"Identificador '{item.name}' duplicado no mesmo {escopo_desc} "
                    f"(já declarado na linha {prev.line})",
                )
            else:
                self.scoped_symbols[scope_key] = entry
                if is_working_storage:
                    self.working_storage_entries.append(entry)

            self.symbols_by_name.setdefault(item.name, []).append(entry)

            # Verifica compatibilidade da cláusula VALUE inicial (quando presente)
            if item.value is not None:
                self._check_initial_value_compatibility(entry, item.value)

        # Registra índices implícitos declarados em OCCURS ... INDEXED BY
        if item.occurs is not None:
            for idx_name in item.occurs.indexed_by:
                if idx_name in ("<ANONIMO>", "<DESCONHECIDO>"):
                    continue
                idx_entry = SymbolEntry(
                    name=idx_name,
                    level=77,
                    category="INDEX",
                    pic=None,
                    ancestors=ancestors,
                    scope_parent="<GLOBAL>",
                    line=item.line,
                    col=item.col,
                )
                scope_key = ("<GLOBAL>", idx_name)
                if scope_key in self.scoped_symbols:
                    prev = self.scoped_symbols[scope_key]
                    self.add_error(
                        item.line,
                        item.col,
                        f"Índice '{idx_name}' duplicado no escopo global (já declarado na linha {prev.line})",
                    )
                else:
                    self.scoped_symbols[scope_key] = idx_entry
                self.symbols_by_name.setdefault(idx_name, []).append(idx_entry)

        # Registra recursivamente os subcampos filhos
        next_ancestors = ancestors + (item.name,)
        for child in item.children:
            self._register_data_item(child, next_ancestors, is_working_storage=is_working_storage)

    def _check_initial_value_compatibility(self, entry: SymbolEntry, val: str) -> None:
        val_upper = val.upper()
        is_num_lit = bool(number_regex.match(val))
        is_zero_const = val_upper in ("ZERO", "ZEROS", "ZEROES")
        is_space_const = val_upper in ("SPACE", "SPACES")

        if entry.category == "NUMERIC":
            if not is_num_lit and not is_zero_const and val_upper not in ("LOW-VALUE", "LOW-VALUES", "HIGH-VALUE", "HIGH-VALUES"):
                self.add_error(
                    entry.line,
                    entry.col,
                    f"Valor inicial '{val}' incompatível com variável numérica '{entry.name}' (PIC {entry.pic})",
                )
        elif entry.category == "ALPHABETIC":
            if is_num_lit or is_zero_const:
                self.add_error(
                    entry.line,
                    entry.col,
                    f"Valor inicial numérico '{val}' incompatível com variável alfabética '{entry.name}' (PIC {entry.pic})",
                )
            elif not is_space_const and any(c.isdigit() for c in val):
                self.add_error(
                    entry.line,
                    entry.col,
                    f"Valor inicial '{val}' contém dígitos e é incompatível com variável alfabética '{entry.name}' (PIC {entry.pic})",
                )

    def _check_file_declarations_consistency(self) -> None:
        # Regra 4: Associação entre FILE-CONTROL (SELECT) e DATA DIVISION (FD)
        for file_name, sel in self.select_files.items():
            if file_name not in self.fd_files:
                self.add_error(
                    sel.line,
                    sel.col,
                    f"Arquivo '{file_name}' declarado em SELECT (FILE-CONTROL) não possui descritor FD correspondente na DATA DIVISION",
                )

        for file_name, fd in self.fd_files.items():
            if file_name not in self.select_files:
                self.add_error(
                    fd.line,
                    fd.col,
                    f"Descritor FD '{file_name}' na DATA DIVISION não foi previamente declarado com SELECT no FILE-CONTROL",
                )

    def _validate_select_variable_refs(self) -> None:
        # Valida se RECORD KEY, ALTERNATE RECORD KEY e FILE STATUS referenciam variáveis existentes
        for sel in self.select_files.values():
            if sel.assign_to in self.symbols_by_name:
                self._mark_symbol_referenced(self.symbols_by_name[sel.assign_to][0])
            for clause in sel.clauses:
                if clause.clause_type in ("RECORD_KEY", "ALTERNATE_RECORD_KEY", "FILE_STATUS"):
                    ref = IdentifierRef(line=sel.line, col=sel.col, name=clause.value)
                    self.resolve_identifier(ref)

    def _mark_symbol_referenced(self, entry: SymbolEntry) -> None:
        self.referenced_names.add(entry.name)
        for anc in entry.ancestors:
            self.referenced_names.add(anc)

    def _check_unused_variables(self) -> None:
        for entry in self.working_storage_entries:
            if entry.name in self.referenced_names:
                continue
            if any(anc in self.referenced_names for anc in entry.ancestors):
                continue
            self.add_error(
                entry.line,
                entry.col,
                f"Sugestão de otimização: variável '{entry.name}' declarada na WORKING-STORAGE SECTION não é referenciada",
                severity="I",
            )

    def _collect_procedure_targets(self) -> None:
        proc = self.program.procedure_division
        if proc is None:
            return

        seen_paragraphs_top: set[str] = set()
        for para in proc.paragraphs:
            if para.name in seen_paragraphs_top:
                self.add_error(
                    para.line,
                    para.col,
                    f"Parágrafo '{para.name}' declarado mais de uma vez na PROCEDURE DIVISION",
                )
            seen_paragraphs_top.add(para.name)
            self.procedures.add(para.name)

        seen_sections: set[str] = set()
        for sec in proc.sections:
            if sec.name in seen_sections:
                self.add_error(
                    sec.line,
                    sec.col,
                    f"Seção '{sec.name}' declarada mais de uma vez na PROCEDURE DIVISION",
                )
            seen_sections.add(sec.name)
            self.procedures.add(sec.name)

            seen_in_sec: set[str] = set()
            for para in sec.paragraphs:
                if para.name in seen_in_sec:
                    self.add_error(
                        para.line,
                        para.col,
                        f"Parágrafo '{para.name}' duplicado dentro da seção '{sec.name}'",
                    )
                seen_in_sec.add(para.name)
                self.procedures.add(para.name)

    # ==========================================================================
    # RESOLUÇÃO DE IDENTIFICADORES E QUALIFICAÇÃO HIERÁRQUICA (REGRA 2)
    # ==========================================================================

    def resolve_identifier(self, ref: IdentifierRef | None) -> SymbolEntry | None:
        if ref is None:
            return None
        if ref.name in ("<DESCONHECIDO>", "<ANONIMO>"):
            return None
        # Se o próprio token já possui erro léxico de formação, evita diagnóstico redundante na mesma posição
        if len(ref.name) > 30 or not user_identifier_regex.match(ref.name):
            return None

        # Valida também os subscritos/índices usados na referência, ex: DPTO(IDX, 1)
        for sub in ref.subscripts:
            self._check_expression(sub)

        candidates = self.symbols_by_name.get(ref.name, [])
        if not candidates:
            self.add_error(
                ref.line,
                ref.col,
                f"Variável '{ref.name}' não foi declarada na DATA DIVISION",
            )
            return None

        # Sem qualificação por OF
        if not ref.qualifiers:
            if len(candidates) > 1:
                # Regra 2: Nomes duplicados exigem qualificação por hierarquia (OF)
                self.add_error(
                    ref.line,
                    ref.col,
                    f"Identificador '{ref.name}' é ambíguo (declarado em múltiplos grupos) "
                    f"e exige qualificação por hierarquia (OF)",
                )
                return None
            self._mark_symbol_referenced(candidates[0])
            return candidates[0]

        # Com qualificação por OF (ex: WRK-TOTAL OF WRK-TOTAIS)
        matched: list[SymbolEntry] = []
        for cand in candidates:
            if self._matches_qualification(cand.ancestors, ref.qualifiers):
                matched.append(cand)

        qual_str = f"{ref.name} OF {' OF '.join(ref.qualifiers)}"
        if not matched:
            self.add_error(
                ref.line,
                ref.col,
                f"Qualificação hierárquica inválida: '{qual_str}' não corresponde a nenhuma declaração na DATA DIVISION",
            )
            return None
        if len(matched) > 1:
            self.add_error(
                ref.line,
                ref.col,
                f"Qualificação hierárquica '{qual_str}' continua ambígua na DATA DIVISION",
            )
            return None

        self._mark_symbol_referenced(matched[0])
        return matched[0]

    @staticmethod
    def _matches_qualification(ancestors: tuple[str, ...], qualifiers: list[str]) -> bool:
        """
        Verifica se a cadeia de qualificadores (do pai mais interno ao mais externo)
        aparece como subsequência válida dos ancestrais (armazenados do nível 01 ao mais interno).
        """
        rev_ancestors = list(reversed(ancestors))
        idx = 0
        for q in qualifiers:
            while idx < len(rev_ancestors) and rev_ancestors[idx] != q:
                idx += 1
            if idx >= len(rev_ancestors):
                return False
            idx += 1
        return True

    # ==========================================================================
    # ANÁLISE DA PROCEDURE DIVISION E REGRAS 1, 2, 3 E 4
    # ==========================================================================

    def _analyze_procedure_division(self) -> None:
        proc = self.program.procedure_division
        if proc is None:
            return

        for sent in proc.sentences:
            self._analyze_sentence(sent)

        for para in proc.paragraphs:
            for sent in para.sentences:
                self._analyze_sentence(sent)

        for sec in proc.sections:
            for sent in sec.sentences:
                self._analyze_sentence(sent)
            for para in sec.paragraphs:
                for sent in para.sentences:
                    self._analyze_sentence(sent)

    def _analyze_sentence(self, sentence: SentenceNode) -> None:
        for stmt in sentence.statements:
            self._analyze_statement(stmt)

    def _analyze_statement(self, stmt: StatementNode) -> None:
        if isinstance(stmt, MoveStmt):
            self._check_move_stmt(stmt)
        elif isinstance(stmt, PerformStmt):
            self._check_perform_stmt(stmt)
        elif isinstance(stmt, GoToStmt):
            self._check_goto_stmt(stmt)
        elif isinstance(stmt, OpenStmt):
            self._check_open_stmt(stmt)
        elif isinstance(stmt, CloseStmt):
            self._check_close_stmt(stmt)
        elif isinstance(stmt, ReadStmt):
            self._check_read_stmt(stmt)
        elif isinstance(stmt, WriteStmt):
            self._check_write_stmt(stmt)
        elif isinstance(stmt, StartStmt):
            self._check_start_stmt(stmt)
        elif isinstance(stmt, DisplayStmt):
            for op in stmt.operands:
                self._check_expression(op)
        elif isinstance(stmt, AcceptStmt):
            self.resolve_identifier(stmt.target)
        elif isinstance(stmt, IfStmt):
            if not stmt.has_end_if:
                self.add_error(
                    stmt.line,
                    stmt.col,
                    "Sugestão de estilo: considere encerrar o bloco condicional IF com 'END-IF' explícito",
                    severity="I",
                )
            self._check_condition(stmt.condition)
            for s in stmt.then_body:
                self._analyze_statement(s)
            for s in stmt.else_body:
                self._analyze_statement(s)
        elif isinstance(stmt, AddStmt):
            for op in stmt.operands:
                self._check_expression(op)
            for t in stmt.to_targets:
                self.resolve_identifier(t)
            for g in stmt.giving_targets:
                self.resolve_identifier(g)
            for s in stmt.on_size_error:
                self._analyze_statement(s)
            for s in stmt.not_on_size_error:
                self._analyze_statement(s)
        elif isinstance(stmt, SubtractStmt):
            for op in stmt.operands:
                self._check_expression(op)
            for t in stmt.from_targets:
                self.resolve_identifier(t)
            for g in stmt.giving_targets:
                self.resolve_identifier(g)
            for s in stmt.on_size_error:
                self._analyze_statement(s)
            for s in stmt.not_on_size_error:
                self._analyze_statement(s)
        elif isinstance(stmt, MultiplyStmt):
            self._check_expression(stmt.operand)
            for t in stmt.by_targets:
                self.resolve_identifier(t)
            for g in stmt.giving_targets:
                self.resolve_identifier(g)
            for s in stmt.on_size_error:
                self._analyze_statement(s)
            for s in stmt.not_on_size_error:
                self._analyze_statement(s)
        elif isinstance(stmt, DivideStmt):
            self._check_expression(stmt.operand)
            for t in stmt.targets:
                self._check_expression(t)
            for g in stmt.giving_targets:
                self.resolve_identifier(g)
            if stmt.remainder is not None:
                self.resolve_identifier(stmt.remainder)
            for s in stmt.on_size_error:
                self._analyze_statement(s)
            for s in stmt.not_on_size_error:
                self._analyze_statement(s)
        elif isinstance(stmt, ComputeStmt):
            for t in stmt.targets:
                self.resolve_identifier(t)
            self._check_expression(stmt.expression)
            for s in stmt.on_size_error:
                self._analyze_statement(s)
            for s in stmt.not_on_size_error:
                self._analyze_statement(s)
        elif isinstance(stmt, EvaluateStmt):
            self._check_expression(stmt.subject)
            for br in stmt.branches:
                if isinstance(br.condition, ExprNode):
                    self._check_expression(br.condition)
                for s in br.body:
                    self._analyze_statement(s)
        elif isinstance(stmt, SearchStmt):
            self.resolve_identifier(stmt.table)
            for s in stmt.at_end:
                self._analyze_statement(s)
            for br in stmt.branches:
                self._check_condition(br.condition)
                for s in br.body:
                    self._analyze_statement(s)
        elif isinstance(stmt, SetStmt):
            for t in stmt.targets:
                self.resolve_identifier(t)
            self._check_expression(stmt.value)
        elif isinstance(stmt, InspectStmt):
            self.resolve_identifier(stmt.target)
            self._check_inspect_clauses(stmt)
        elif isinstance(stmt, StopRunStmt):
            pass

    def _check_inspect_clauses(self, stmt: InspectStmt) -> None:
        # Em INSPECT <id> TALLYING <contador> FOR ..., o primeiro token de clauses após TALLYING é o contador
        if stmt.mode == "TALLYING" and stmt.clauses:
            counter_name = stmt.clauses[0]
            if not counter_name.startswith('"') and counter_name not in (
                "FOR", "CHARACTERS", "ALL", "LEADING", "BEFORE", "AFTER", "INITIAL"
            ):
                ref = IdentifierRef(line=stmt.line, col=stmt.col, name=counter_name)
                self.resolve_identifier(ref)

    # --- Regra 1: Compatibilidade de MOVE ---

    def _check_move_stmt(self, stmt: MoveStmt) -> None:
        source_entry: SymbolEntry | None = None
        if isinstance(stmt.source, IdentifierRef):
            source_entry = self.resolve_identifier(stmt.source)
        else:
            self._check_expression(stmt.source)

        for target_ref in stmt.targets:
            target_entry = self.resolve_identifier(target_ref)
            if target_entry is None:
                continue

            # 1. Origem é uma variável declarada
            if source_entry is not None:
                # Regra 4.1 explícita: Impedir movimentação de PIC A (ALPHABETIC) para PIC 9 (NUMERIC)
                if source_entry.category == "ALPHABETIC" and target_entry.category == "NUMERIC":
                    self.add_error(
                        stmt.line,
                        stmt.col,
                        f"Incompatibilidade de MOVE: não é permitido mover dados puramente alfabéticos "
                        f"'{source_entry.name}' (PIC {source_entry.pic}) para variável numérica "
                        f"'{target_entry.name}' (PIC {target_entry.pic})",
                    )
                # Impedir movimentação de variável numérica (PIC 9) para variável puramente alfabética (PIC A)
                elif source_entry.category in ("NUMERIC", "INDEX") and target_entry.category == "ALPHABETIC":
                    self.add_error(
                        stmt.line,
                        stmt.col,
                        f"Incompatibilidade de MOVE: não é permitido mover dados numéricos "
                        f"'{source_entry.name}' para variável puramente alfabética "
                        f"'{target_entry.name}' (PIC {target_entry.pic})",
                    )
                elif source_entry.pic is not None and target_entry.pic is not None:
                    src_sz = compute_pic_byte_size(source_entry.pic)
                    dst_sz = compute_pic_byte_size(target_entry.pic)
                    if src_sz > dst_sz:
                        self.add_error(
                            stmt.line,
                            stmt.col,
                            f"Potencial truncamento em MOVE: variável de origem '{source_entry.name}' "
                            f"({src_sz} bytes) é maior que o destino '{target_entry.name}' ({dst_sz} bytes)",
                            severity="W",
                        )

            # 2. Origem é um literal ou constante figurativa
            elif isinstance(stmt.source, LiteralNode):
                lit = stmt.source
                lit_upper = lit.value.upper()

                if target_entry.category == "NUMERIC":
                    if lit.literal_type == "ALPHANUMERIC_LITERAL" and not number_regex.match(lit.value.strip()):
                        self.add_error(
                            stmt.line,
                            stmt.col,
                            f"Incompatibilidade de MOVE: não é permitido mover literal alfanumérico "
                            f"'{lit.value}' para variável numérica '{target_entry.name}' (PIC {target_entry.pic})",
                        )
                    elif lit.literal_type == "FIGURATIVE_CONSTANT" and lit_upper in ("SPACE", "SPACES"):
                        self.add_error(
                            stmt.line,
                            stmt.col,
                            f"Incompatibilidade de MOVE: não é permitido mover '{lit.value}' "
                            f"para variável numérica '{target_entry.name}' (PIC {target_entry.pic})",
                        )
                elif target_entry.category == "ALPHABETIC":
                    if lit.literal_type == "NUMBER" or (
                        lit.literal_type == "FIGURATIVE_CONSTANT" and lit_upper in ("ZERO", "ZEROS", "ZEROES")
                    ):
                        self.add_error(
                            stmt.line,
                            stmt.col,
                            f"Incompatibilidade de MOVE: não é permitido mover valor numérico "
                            f"'{lit.value}' para variável puramente alfabética '{target_entry.name}' (PIC {target_entry.pic})",
                        )
                    elif lit.literal_type == "ALPHANUMERIC_LITERAL" and any(c.isdigit() for c in lit.value):
                        self.add_error(
                            stmt.line,
                            stmt.col,
                            f"Incompatibilidade de MOVE: literal '{lit.value}' contém dígitos e não pode ser "
                            f"movido para variável puramente alfabética '{target_entry.name}' (PIC {target_entry.pic})",
                        )

    # --- Regra 3: Validação de Destinos de Controle (PERFORM / GO TO) ---

    def _check_perform_stmt(self, stmt: PerformStmt) -> None:
        if stmt.target not in ("<DESCONHECIDO>", "<ANONIMO>"):
            if stmt.target not in self.procedures:
                self.add_error(
                    stmt.line,
                    stmt.col,
                    f"Destino de controle inválido: parágrafo ou seção '{stmt.target}' "
                    f"referenciado em PERFORM não foi declarado na PROCEDURE DIVISION",
                )

        if stmt.thru is not None and stmt.thru not in ("<DESCONHECIDO>", "<ANONIMO>"):
            if stmt.thru not in self.procedures:
                self.add_error(
                    stmt.line,
                    stmt.col,
                    f"Destino de controle inválido: parágrafo ou seção '{stmt.thru}' "
                    f"referenciado em PERFORM THRU não foi declarado na PROCEDURE DIVISION",
                )

        if stmt.until is not None:
            self._check_condition(stmt.until)
        if stmt.times is not None:
            self._check_expression(stmt.times)

    def _check_goto_stmt(self, stmt: GoToStmt) -> None:
        if stmt.target not in ("<DESCONHECIDO>", "<ANONIMO>"):
            self.add_error(
                stmt.line,
                stmt.col,
                f"Uso de 'GO TO {stmt.target}' pode gerar fluxo de controle ambíguo; prefira estruturar com PERFORM",
                severity="W",
            )
            if stmt.target not in self.procedures:
                self.add_error(
                    stmt.line,
                    stmt.col,
                    f"Destino de controle inválido: parágrafo ou seção '{stmt.target}' "
                    f"referenciado em GO TO não foi declarado na PROCEDURE DIVISION",
                )

    # --- Regra 4: Associação de Arquivos (OPEN, READ, WRITE, CLOSE, START) ---

    def _verify_file_association(self, file_name: str, operation: str, line: int, col: int) -> None:
        if file_name in ("<DESCONHECIDO>", "<ANONIMO>"):
            return
        in_select = file_name in self.select_files
        in_fd = file_name in self.fd_files

        if not in_select and not in_fd:
            self.add_error(
                line,
                col,
                f"Operação {operation} inválida: arquivo '{file_name}' não foi declarado "
                f"no FILE-CONTROL (SELECT) nem possui descritor FD na DATA DIVISION",
            )
        elif not in_select:
            self.add_error(
                line,
                col,
                f"Operação {operation} inválida: arquivo '{file_name}' exige prévia declaração "
                f"SELECT no FILE-CONTROL",
            )
        elif not in_fd:
            self.add_error(
                line,
                col,
                f"Operação {operation} inválida: arquivo '{file_name}' exige respectivo descritor "
                f"FD na DATA DIVISION",
            )

    def _check_open_stmt(self, stmt: OpenStmt) -> None:
        for group in stmt.groups:
            for file_name in group.files:
                self._verify_file_association(file_name, "OPEN", stmt.line, stmt.col)

    def _check_close_stmt(self, stmt: CloseStmt) -> None:
        for file_name in stmt.files:
            self._verify_file_association(file_name, "CLOSE", stmt.line, stmt.col)

    def _check_read_stmt(self, stmt: ReadStmt) -> None:
        self._verify_file_association(stmt.file_name, "READ", stmt.line, stmt.col)
        if stmt.into is not None:
            self.resolve_identifier(stmt.into)
        if stmt.key is not None:
            self.resolve_identifier(stmt.key)
        for s in stmt.invalid_key:
            self._analyze_statement(s)
        for s in stmt.not_invalid_key:
            self._analyze_statement(s)
        for s in stmt.at_end:
            self._analyze_statement(s)
        for s in stmt.not_at_end:
            self._analyze_statement(s)

    def _check_write_stmt(self, stmt: WriteStmt) -> None:
        rec = stmt.record_name
        if rec not in ("<DESCONHECIDO>", "<ANONIMO>"):
            if rec in self.record_to_fd:
                fd_file = self.record_to_fd[rec]
                # Verifica se o arquivo dono do registro está devidamente associado no FILE-CONTROL e FD
                self._verify_file_association(fd_file, f"WRITE ({rec})", stmt.line, stmt.col)
            elif rec in self.select_files or rec in self.fd_files:
                self._verify_file_association(rec, "WRITE", stmt.line, stmt.col)
            else:
                self.add_error(
                    stmt.line,
                    stmt.col,
                    f"Operação WRITE inválida: registro '{rec}' não pertence a nenhum descritor FD "
                    f"declarado na DATA DIVISION com respectivo arquivo no FILE-CONTROL",
                )

        if stmt.from_id is not None:
            self.resolve_identifier(stmt.from_id)
        for s in stmt.invalid_key:
            self._analyze_statement(s)
        for s in stmt.not_invalid_key:
            self._analyze_statement(s)

    def _check_start_stmt(self, stmt: StartStmt) -> None:
        self._verify_file_association(stmt.file_name, "START", stmt.line, stmt.col)
        if stmt.key_name is not None:
            self.resolve_identifier(stmt.key_name)
        for s in stmt.invalid_key:
            self._analyze_statement(s)
        for s in stmt.not_invalid_key:
            self._analyze_statement(s)

    # --- Validação de Expressões e Condições ---

    def _check_expression(self, expr: ExprNode | None) -> None:
        if expr is None:
            return
        if isinstance(expr, IdentifierRef):
            self.resolve_identifier(expr)
        elif isinstance(expr, FunctionCallNode):
            for arg in expr.arguments:
                self._check_expression(arg)
        elif isinstance(expr, UnaryExprNode):
            self._check_expression(expr.operand)
        elif isinstance(expr, BinaryExprNode):
            self._check_expression(expr.left)
            self._check_expression(expr.right)

    def _check_condition(self, cond: ConditionNode | None) -> None:
        if cond is None:
            return
        if isinstance(cond, SimpleConditionNode):
            self._check_expression(cond.operand)
        elif isinstance(cond, RelationConditionNode):
            self._check_expression(cond.left)
            self._check_expression(cond.right)
        elif isinstance(cond, NotConditionNode):
            self._check_condition(cond.operand)
        elif isinstance(cond, LogicalConditionNode):
            self._check_condition(cond.left)
            self._check_condition(cond.right)


# ==============================================================================
# FUNÇÃO PÚBLICA E PONTO DE ENTRADA (CLI)
# ==============================================================================


def analyze(
    program: ProgramNode, errors: list[CompileError] | None = None
) -> tuple[bool, list[CompileError]]:
    analyzer = SemanticAnalyzer(program, errors)
    return analyzer.analyze()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python semantic_analyzer.py <arquivo_fonte.cob> [--debug]")
        sys.exit(1)

    caminho_arquivo = sys.argv[1]
    modo_depuracao = "--debug" in sys.argv[2:]

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

        # 4. Exibição do Flag de Validação e Diagnósticos
        erros_graves = [e for e in erros if e.severity in ("E", "S")]
        avisos = [e for e in erros if e.severity in ("W", "I")]

        if valido:
            print(f"[OK] VALIDAÇÃO CONCLUÍDA COM SUCESSO (0 erros, {len(avisos)} aviso(s))")
            if avisos:
                print()
                print(f"{'SEV':<5} | {'LINHA':<7} | {'COL':<5} | {'MENSAGEM'}")
                print("-" * 70)
                for aviso in avisos:
                    print(f"{aviso.severity:<5} | {aviso.line:<7} | {aviso.col:<5} | {aviso.message}")
            sys.exit(0)
        else:
            print(
                f"[FALHA] VALIDAÇÃO REPROVADA ({len(erros_graves)} erro(s) impeditivo(s), {len(avisos)} aviso(s))"
            )
            print()
            print(f"{'SEV':<5} | {'LINHA':<7} | {'COL':<5} | {'MENSAGEM'}")
            print("-" * 70)
            for erro in erros:
                print(f"{erro.severity:<5} | {erro.line:<7} | {erro.col:<5} | {erro.message}")
            sys.exit(1)

    except FileNotFoundError:
        print(f"Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
        sys.exit(1)
