#!/usr/bin/env python3
# Integrantes do grupo 9:
#   Thiago Costa    - Nº USP 14609529
#   Oliver Kuramae  - Nº USP 14601326
#   Theo Brito      - Nº USP 13688367

"""
cobython.py — Driver unificado de linha de comando (estilo GCC / Clang) para o compilador COBython.

Pipeline de compilação:
  1. Análise Léxica e Expansão de Copybooks (lexer.py)
  2. Análise Sintática / Construção da AST (parser.py)
  3. Análise Semântica / Verificações de Tipo, Escopo e Arquivos (semantic_analyzer.py)
  4. Geração de Código / Middle-End & Back-End Plugável (padrão: llvm_codegen.py -> LLVM IR .ll)
  5. Otimização, Montagem e Linkedição via infraestrutura LLVM (clang / opt / llc)
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

from lexer import (
    CompileError,
    Token,
    expand_copybooks,
    find_copybook,
    make_token_stream,
)
from llvm_codegen import compile_with_llvm_toolchain, generate_llvm_ir
from parser import ProgramNode, format_ast, parse
from semantic_analyzer import analyze

VERSION = "1.0.0"

# Garante que plugins externos que façam `from cobython import CodegenBackend`
# referenciem a mesma instância do módulo quando executado como script principal (__main__).
sys.modules.setdefault("cobython", sys.modules[__name__])

# ==============================================================================
# OPÇÕES DE COMPILAÇÃO E PROTOCOLO DE BACKENDS PLUGÁVEIS
# ==============================================================================


@dataclass
class CompilerOptions:
    input_files: list[str]
    output_file: str | None = None
    opt_level: str = "-O0"           # -O0, -O1, -O2, -O3, -Os, -Oz
    emit_llvm: bool = False          # -emit-llvm
    compile_only: bool = False       # -c (gera .o ou .bc sem linkeditar)
    assembly_only: bool = False      # -S (gera .s ou .ll)
    syntax_only: bool = False        # -fsyntax-only
    debug_mode: bool = False         # -g / --debug
    include_dirs: list[str] = field(default_factory=list)  # -I <dir>
    backend_name: str = "llvm"       # --backend <nome_ou_caminho.py>
    extra_llvm_flags: list[str] = field(default_factory=list)  # -Xllvm <flag>
    suppress_warnings: bool = False  # -w
    no_info: bool = False            # -Wno-info
    warnings_as_errors: bool = False # -Werror
    dump_tokens: bool = False        # --dump-tokens
    dump_ast: bool = False           # --dump-ast
    keep_temps: bool = False         # -save-temps
    verbose: bool = False            # -v / --verbose


class CodegenBackend(ABC):
    """
    Interface base para acoplamento de geradores de código (Middle-End / Back-End) ao COBython.
    Permite acrescentar transpiladores (ex: C, Python) ou geradores de assembly direto.
    """

    name: str = "custom"
    description: str = "Backend customizado"
    default_extension: str = ".out"

    @abstractmethod
    def generate(self, program: ProgramNode, options: CompilerOptions) -> str:
        """Recebe a AST validada (ProgramNode) e retorna o código intermediário/alvo textual."""
        raise NotImplementedError

    def compile_output(
        self,
        generated_path: str,
        output_path: str,
        options: CompilerOptions,
    ) -> tuple[bool, str, list[str]]:
        """
        Etapa opcional de pós-processamento (otimização, montagem e linkedição)
        sobre o arquivo textual gerado por `generate()`.
        Retorna (sucesso, mensagem, lista_de_comandos_executados).
        """
        if os.path.abspath(generated_path) != os.path.abspath(output_path):
            with open(generated_path, "r", encoding="utf-8") as src, open(
                output_path, "w", encoding="utf-8"
            ) as dst:
                dst.write(src.read())
        return True, f"Código alvo gerado pelo backend '{self.name}' em: '{output_path}'", []


class LLVMBackend(CodegenBackend):
    """Middle-end e Back-end padrão baseado em Representação Intermediária LLVM (.ll)."""

    name = "llvm"
    description = "Gerador de Representação Intermediária da LLVM (.ll) via llvm_codegen.py (Padrão)"
    default_extension = ".ll"

    def generate(self, program: ProgramNode, options: CompilerOptions) -> str:
        return generate_llvm_ir(program)

    def compile_output(
        self,
        generated_path: str,
        output_path: str,
        options: CompilerOptions,
    ) -> tuple[bool, str, list[str]]:
        output_mode = resolve_output_mode(options)
        return compile_with_llvm_toolchain(
            ll_path=generated_path,
            output_path=output_path,
            opt_level=options.opt_level,
            output_mode=output_mode,
            debug_info=options.debug_mode,
            extra_flags=options.extra_llvm_flags,
            verbose=options.verbose,
        )


class FunctionPluginBackend(CodegenBackend):
    """Adaptador para módulos Python externos que expõem uma função `generate(program, options)`."""

    def __init__(
        self,
        name: str,
        gen_fn: Callable[[ProgramNode, CompilerOptions], str],
        compile_fn: Callable[[str, str, CompilerOptions], tuple[bool, str, list[str]]] | None = None,
        ext: str = ".out",
        desc: str = "Plugin carregado dinamicamente",
    ):
        self.name = name
        self._gen_fn = gen_fn
        self._compile_fn = compile_fn
        self.default_extension = ext
        self.description = desc

    def generate(self, program: ProgramNode, options: CompilerOptions) -> str:
        try:
            return self._gen_fn(program, options)
        except TypeError:
            # Permite funções simples `generate(program)` de 1 argumento
            return self._gen_fn(program)  # type: ignore[call-arg]

    def compile_output(
        self,
        generated_path: str,
        output_path: str,
        options: CompilerOptions,
    ) -> tuple[bool, str, list[str]]:
        if self._compile_fn is not None:
            return self._compile_fn(generated_path, output_path, options)
        return super().compile_output(generated_path, output_path, options)


# Registro global de backends disponíveis
BACKEND_REGISTRY: dict[str, CodegenBackend] = {
    "llvm": LLVMBackend(),
}


def register_backend(backend: CodegenBackend) -> None:
    """Registra um novo backend (transpilador ou gerador de assembly) no compilador."""
    BACKEND_REGISTRY[backend.name.lower()] = backend


def load_backend(spec: str) -> CodegenBackend:
    """
    Obtém um backend registrado pelo nome (ex: 'llvm') ou carrega dinamicamente
    um módulo Python externo (.py) contendo:
      - Uma subclasse de `CodegenBackend` (ou classe chamada `Backend`), OU
      - Uma função fábrica `create_backend()`, OU
      - Uma função `generate(program, [options])`.
    """
    key = spec.lower()
    if key in BACKEND_REGISTRY:
        return BACKEND_REGISTRY[key]

    module_path = spec if spec.endswith(".py") else spec + ".py"
    if not os.path.isfile(module_path):
        raise ValueError(
            f"Backend '{spec}' não encontrado. Backends embutidos: {', '.join(BACKEND_REGISTRY)} "
            f"(ou informe o caminho de um arquivo Python .py)"
        )

    mod_name = os.path.splitext(os.path.basename(module_path))[0]
    spec_obj = importlib.util.spec_from_file_location(mod_name, module_path)
    if spec_obj is None or spec_obj.loader is None:
        raise ValueError(f"Não foi possível carregar o módulo de backend '{module_path}'")

    module = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(module)

    if hasattr(module, "create_backend") and callable(module.create_backend):
        backend = module.create_backend()
        if isinstance(backend, CodegenBackend):
            return backend

    if hasattr(module, "Backend") and isinstance(module.Backend, type):
        return module.Backend()

    for attr_val in vars(module).values():
        if (
            isinstance(attr_val, type)
            and attr_val is not CodegenBackend
            and attr_val.__name__ != "CodegenBackend"
            and (
                issubclass(attr_val, CodegenBackend)
                or callable(getattr(attr_val, "generate", None))
            )
        ):
            return attr_val()

    if hasattr(module, "generate") and callable(module.generate):
        ext = getattr(module, "DEFAULT_EXTENSION", ".out")
        desc = getattr(module, "DESCRIPTION", f"Plugin externo ({os.path.basename(module_path)})")
        comp_fn = getattr(module, "compile_output", None)
        return FunctionPluginBackend(mod_name, module.generate, comp_fn, ext, desc)

    raise ValueError(
        f"O módulo '{module_path}' não define uma subclasse de CodegenBackend nem uma função generate(program)"
    )


def resolve_output_mode(options: CompilerOptions) -> str:
    if options.emit_llvm:
        return "bitcode" if options.compile_only else "llvm-ir"
    if options.assembly_only:
        return "assembly"
    if options.compile_only:
        return "object"
    return "executable"


def default_output_path(input_path: str, options: CompilerOptions, backend: CodegenBackend) -> str:
    base = os.path.splitext(input_path)[0]
    if backend.name != "llvm":
        return base + backend.default_extension
    mode = resolve_output_mode(options)
    match mode:
        case "llvm-ir":
            return base + ".ll"
        case "bitcode":
            return base + ".bc"
        case "assembly":
            return base + ".s"
        case "object":
            return base + ".o"
        case _:
            return base


# ==============================================================================
# SEÇÃO 6: SISTEMA DE NOTIFICAÇÃO DE ERROS E DIRETIVAS DO COMPILADOR
# Níveis de Severidade:
#   I (Informational): Sugestões de estilo ou otimização.
#   W (Warning): Alertas de potenciais ambiguidades (não impedem a compilação).
#   E (Error): Erros sintáticos ou semânticos graves (impedem a linkedição).
#   S (Severe): Falha catastrófica no processo de compilação.
# ==============================================================================

SEVERITY_LABELS = {
    "I": ("INFORMATIONAL", "\033[36m"),  # Ciano
    "W": ("WARNING", "\033[33m"),        # Amarelo
    "E": ("ERROR", "\033[31m"),          # Vermelho
    "S": ("SEVERE", "\033[1;35m"),       # Magenta negrito
}
RESET_COLOR = "\033[0m"
BOLD_COLOR = "\033[1m"


@dataclass
class StageDiagnostic:
    stage: str          # LEXER, PARSER, SEMANTIC, DRIVER
    error: CompileError


def expand_copybooks_with_include_dirs(
    token_stream: list[Token],
    errors: list[CompileError],
    search_dirs: list[str],
    debugging_mode: bool = False,
    open_copybooks: frozenset[str] = frozenset(),
) -> list[Token]:
    """
    Suporte à diretiva COPY [Nome]. (Seção 6 da Especificação) buscando no diretório
    do arquivo atual e nos diretórios adicionais passados via -I <dir>.
    """
    expanded: list[Token] = []
    i = 0
    while i < len(token_stream):
        token = token_stream[i]
        if token.type == "COPY":
            if (
                i + 2 < len(token_stream)
                and token_stream[i + 1].type in ("VALID_ID", "ALPHANUMERIC_LITERAL")
                and token_stream[i + 2].type == "DOT"
            ):
                name = token_stream[i + 1].value.strip()
                copybook_path: str | None = None
                for d in search_dirs:
                    copybook_path = find_copybook(d, name)
                    if copybook_path is not None:
                        break
                if copybook_path is None:
                    errors.append(
                        CompileError("E", token.line, token.col, f"Copybook '{name}' não encontrado")
                    )
                elif copybook_path in open_copybooks:
                    errors.append(
                        CompileError(
                            "S", token.line, token.col, f"Inclusão recursiva do copybook '{name}'"
                        )
                    )
                else:
                    try:
                        with open(copybook_path, "r", encoding="utf-8") as copybook:
                            copy_tokens, copy_errors = make_token_stream(
                                copybook.readlines(), debugging_mode
                            )
                        errors.extend(copy_errors)
                        sub_dirs = [os.path.dirname(copybook_path)] + [
                            d for d in search_dirs if d != os.path.dirname(copybook_path)
                        ]
                        expanded.extend(
                            expand_copybooks_with_include_dirs(
                                copy_tokens,
                                errors,
                                sub_dirs,
                                debugging_mode,
                                open_copybooks | {copybook_path},
                            )
                        )
                    except OSError as exc:
                        errors.append(
                            CompileError(
                                "S",
                                token.line,
                                token.col,
                                f"Falha catastrófica ao ler copybook '{name}': {exc}",
                            )
                        )
                i += 3
                continue
            errors.append(
                CompileError(
                    "E", token.line, token.col, "Diretiva COPY mal formada (esperado: COPY [Nome].)"
                )
            )
        expanded.append(token)
        i += 1
    return expanded


def apply_diagnostic_filters(
    diagnostics: list[StageDiagnostic], options: CompilerOptions
) -> list[StageDiagnostic]:
    """Aplica flags de controle de severidade (-w, -Wno-info, -Werror)."""
    filtered: list[StageDiagnostic] = []
    for diag in diagnostics:
        sev = diag.error.severity
        if options.suppress_warnings and sev in ("I", "W"):
            continue
        if options.no_info and sev == "I":
            continue
        if options.warnings_as_errors and sev == "W":
            diag = StageDiagnostic(
                stage=diag.stage,
                error=CompileError(
                    "E",
                    diag.error.line,
                    diag.error.col,
                    f"{diag.error.message} [-Werror]",
                ),
            )
        filtered.append(diag)
    return filtered


def print_diagnostics_report(
    source_path: str,
    source_lines: list[str],
    diagnostics: list[StageDiagnostic],
    use_color: bool = True,
) -> None:
    """
    Imprime a notificação de erros conforme a Seção 6 da ESPECIFICAÇÃO TEXTUAL.txt,
    combinando formato rico estilo GCC/Clang (com trecho de código e cursor '^')
    e o sumário por nível de severidade (I, W, E, S).
    """
    if not diagnostics:
        return

    b = BOLD_COLOR if use_color else ""
    r = RESET_COLOR if use_color else ""

    for diag in diagnostics:
        err = diag.error
        sev_name, color = SEVERITY_LABELS.get(err.severity, ("ERROR", ""))
        c = color if use_color else ""
        loc = f"{source_path}:{err.line}:{err.col}"
        print(
            f"{b}{loc}:{r} {c}[{err.severity} - {sev_name}] ({diag.stage}){r}: {err.message}",
            file=sys.stderr,
        )
        if 1 <= err.line <= len(source_lines):
            raw_line = source_lines[err.line - 1].rstrip("\r\n").expandtabs(8)
            line_num_str = f"{err.line:>5}"
            caret_pos = max(0, err.col - 1)
            caret_line = " " * caret_pos + f"{c}^{r}"
            print(f" {line_num_str} | {raw_line}", file=sys.stderr)
            print(f"       | {caret_line}", file=sys.stderr)

    counts = {"S": 0, "E": 0, "W": 0, "I": 0}
    for d in diagnostics:
        if d.error.severity in counts:
            counts[d.error.severity] += 1

    print("-" * 74, file=sys.stderr)
    print(
        f"Resumo de Diagnósticos ({source_path}): "
        f"{counts['S']} Severe (S), {counts['E']} Error (E), "
        f"{counts['W']} Warning (W), {counts['I']} Informational (I).",
        file=sys.stderr,
    )


# ==============================================================================
# EXECUÇÃO DO PIPELINE DE COMPILAÇÃO
# ==============================================================================


def compile_single_file(source_path: str, options: CompilerOptions, backend: CodegenBackend) -> bool:
    use_color = sys.stderr.isatty()
    all_diagnostics: list[StageDiagnostic] = []

    # 0. Leitura do Arquivo Fonte (Erros de I/O geram nível S - Severe Error)
    if not os.path.isfile(source_path):
        all_diagnostics.append(
            StageDiagnostic(
                "DRIVER",
                CompileError(
                    "S",
                    1,
                    1,
                    f"Falha catastrófica: arquivo fonte '{source_path}' não encontrado",
                ),
            )
        )
        print_diagnostics_report(source_path, [], all_diagnostics, use_color)
        return False

    try:
        with open(source_path, "r", encoding="utf-8") as f:
            source_lines = f.readlines()
    except OSError as exc:
        all_diagnostics.append(
            StageDiagnostic(
                "DRIVER",
                CompileError(
                    "S",
                    1,
                    1,
                    f"Falha catastrófica de leitura em '{source_path}': {exc}",
                ),
            )
        )
        print_diagnostics_report(source_path, [], all_diagnostics, use_color)
        return False

    if options.verbose:
        print(f"[COBython] 1/4 Executando Análise Léxica e COPY em '{source_path}'...")

    # 1. Etapa Léxica e Diretiva COPY (lexer.py)
    tokens, lex_errors = make_token_stream(source_lines, options.debug_mode)
    base_dir = os.path.dirname(os.path.abspath(source_path))
    search_dirs = [base_dir] + [os.path.abspath(d) for d in options.include_dirs]
    tokens = expand_copybooks_with_include_dirs(
        tokens, lex_errors, search_dirs, options.debug_mode
    )

    for err in lex_errors:
        all_diagnostics.append(StageDiagnostic("LEXER", err))

    if options.dump_tokens:
        print(f"{'LINHA':<7} | {'COL':<5} | {'ÁREA':<10} | {'TIPO DO TOKEN':<25} | {'VALOR'}")
        print("-" * 70)
        for tok in tokens:
            print(f"{tok.line:<7} | {tok.col:<5} | {tok.area:<10} | {tok.type:<25} | {tok.value}")
        print()

    # Se houve erro catastrófico (S) na etapa léxica (ex: copybook recursivo) ou arquivo sem tokens
    if not tokens and not any(d.error.severity == "S" for d in all_diagnostics):
        all_diagnostics.append(
            StageDiagnostic(
                "LEXER",
                CompileError(
                    "S",
                    1,
                    1,
                    "Falha catastrófica: nenhum token COBOL válido encontrado no arquivo fonte",
                ),
            )
        )

    if any(d.error.severity == "S" for d in all_diagnostics):
        visible = apply_diagnostic_filters(all_diagnostics, options)
        print_diagnostics_report(source_path, source_lines, visible, use_color)
        return False

    if options.verbose:
        print(f"[COBython] 2/4 Executando Análise Sintática (parser.py)...")

    # 2. Etapa Sintática (parser.py)
    ast, parse_errors = parse(tokens, [])
    for err in parse_errors:
        all_diagnostics.append(StageDiagnostic("PARSER", err))

    if options.dump_ast:
        print("ÁRVORE SINTÁTICA ABSTRATA (AST)")
        print("=" * 70)
        print(format_ast(ast))
        print()

    if options.verbose:
        print(f"[COBython] 3/4 Executando Análise Semântica (semantic_analyzer.py)...")

    # 3. Etapa Semântica (semantic_analyzer.py)
    _, sem_errors = analyze(ast, [])
    for err in sem_errors:
        all_diagnostics.append(StageDiagnostic("SEMANTIC", err))

    # Aplica filtros de severidade (-w, -Wno-info, -Werror) e exibe diagnósticos
    visible_diagnostics = apply_diagnostic_filters(all_diagnostics, options)
    print_diagnostics_report(source_path, source_lines, visible_diagnostics, use_color)

    # Verifica se há erros impeditivos (E ou S) conforme a Seção 6
    has_blocking_errors = any(
        d.error.severity in ("E", "S") for d in visible_diagnostics
    ) or any(d.error.severity in ("E", "S") for d in all_diagnostics)

    if has_blocking_errors:
        print(
            f"[FALHA] Compilação de '{source_path}' interrompida: erros de nível E/S impedem a geração de código e linkedição.",
            file=sys.stderr,
        )
        return False

    if options.syntax_only:
        if options.verbose:
            print(f"[COBython] Validação concluída com sucesso (-fsyntax-only) para '{source_path}'.")
        return True

    # 4. Geração de Código e Acionamento do Backend (Padrão: llvm_codegen.py)
    output_path = options.output_file or default_output_path(source_path, options, backend)
    if options.verbose:
        print(
            f"[COBython] 4/4 Gerando código com backend '{backend.name}' "
            f"(otimização {options.opt_level}) -> '{output_path}'..."
        )

    try:
        generated_code = backend.generate(ast, options)
    except Exception as exc:
        err_diag = StageDiagnostic(
            "DRIVER",
            CompileError("S", 1, 1, f"Falha catastrófica no backend '{backend.name}': {exc}"),
        )
        print_diagnostics_report(source_path, source_lines, [err_diag], use_color)
        return False

    output_mode = resolve_output_mode(options)
    if backend.name == "llvm" and output_mode == "llvm-ir" and options.opt_level == "-O0":
        with open(output_path, "w", encoding="utf-8") as out_f:
            out_f.write(generated_code)
        if options.verbose:
            print(f"[COBython] Representação Intermediária LLVM escrita em '{output_path}'.")
        return True

    # Salva arquivo intermediário temporário (ou permanente com -save-temps)
    if options.keep_temps:
        inter_path = os.path.splitext(output_path)[0] + backend.default_extension
        with open(inter_path, "w", encoding="utf-8") as out_f:
            out_f.write(generated_code)
        ok, msg, cmds = backend.compile_output(inter_path, output_path, options)
    else:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=backend.default_extension,
            prefix="cobython_",
             encoding="utf-8",
            delete=False,
        ) as tmp_f:
            tmp_f.write(generated_code)
            tmp_path = tmp_f.name
        try:
            ok, msg, cmds = backend.compile_output(tmp_path, output_path, options)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    if options.verbose:
        for c in cmds:
            print(f"  $ {c}")
        print(f"[COBython] {msg}")

    if not ok:
        err_diag = StageDiagnostic("DRIVER", CompileError("S", 1, 1, msg))
        print_diagnostics_report(source_path, source_lines, [err_diag], use_color)
        return False

    return True


# ==============================================================================
# PARSER DE ARGUMENTOS DE LINHA DE COMANDO (ESTILO GCC / CLANG)
# ==============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cobython",
        description="COBython — Compilador COBOL com Middle-End LLVM IR e Backends Plugáveis.",
        epilog=(
            "Exemplos de uso:\n"
            "  python3 cobython.py programa.cob -o programa\n"
            "  python3 cobython.py programa.cob -O3 -o programa_otimizado\n"
            "  python3 cobython.py programa.cob -S -emit-llvm -O2 -o programa.ll\n"
            "  python3 cobython.py programa.cob -S -O2 -o programa.s\n"
            "  python3 cobython.py programa.cob -c -o programa.o\n"
            "  python3 cobython.py programa.cob -fsyntax-only -Wall\n"
            "  python3 cobython.py programa.cob --backend meu_transpilador.py -o saida.c"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "inputs",
        nargs="*",
        metavar="ARQUIVO.cob",
        help="Arquivo(s) fonte COBOL a serem compilados",
    )
    parser.add_argument(
        "-o",
        dest="output",
        metavar="ARQUIVO",
        help="Caminho do arquivo de saída (executável, .o, .s ou .ll)",
    )

    # Controle de fases (estilo GCC/Clang)
    phase_group = parser.add_argument_group("Controle de Fases de Compilação")
    phase_group.add_argument(
        "-fsyntax-only",
        action="store_true",
        help="Executa apenas análise léxica, sintática e semântica, sem gerar código",
    )
    phase_group.add_argument(
        "-S",
        dest="assembly_only",
        action="store_true",
        help="Compila apenas até Assembly nativo (.s) ou LLVM IR (.ll com -emit-llvm)",
    )
    phase_group.add_argument(
        "-c",
        dest="compile_only",
        action="store_true",
        help="Compila e monta gerando código objeto (.o) ou bitcode (.bc), sem linkeditar",
    )
    phase_group.add_argument(
        "-emit-llvm",
        "--emit-llvm",
        dest="emit_llvm",
        action="store_true",
        help="Emite Representação Intermediária da LLVM (.ll com -S ou por padrão, .bc com -c)",
    )
    phase_group.add_argument(
        "-save-temps",
        action="store_true",
        help="Preserva os arquivos intermediários (.ll) gerados durante a compilação",
    )

    # Flags de otimização (repassadas para o LLVM)
    opt_group = parser.add_argument_group("Níveis de Otimização (repassados ao LLVM)")
    opt_group.add_argument("-O0", dest="opt_level", action="store_const", const="-O0", help="Sem otimização (padrão)")
    opt_group.add_argument("-O1", dest="opt_level", action="store_const", const="-O1", help="Otimização básica (-O1)")
    opt_group.add_argument("-O2", dest="opt_level", action="store_const", const="-O2", help="Otimização moderada (-O2)")
    opt_group.add_argument("-O3", dest="opt_level", action="store_const", const="-O3", help="Otimização agressiva (-O3)")
    opt_group.add_argument("-Os", dest="opt_level", action="store_const", const="-Os", help="Otimizar para tamanho de código (-Os)")
    opt_group.add_argument("-Oz", dest="opt_level", action="store_const", const="-Oz", help="Otimizar agressivamente para tamanho (-Oz)")

    # Diretivas, Depuração e Diagnósticos (Seção 6)
    diag_group = parser.add_argument_group("Diretivas, Depuração e Diagnósticos (Seção 6)")
    diag_group.add_argument(
        "-I",
        dest="include_dirs",
        action="append",
        default=[],
        metavar="DIR",
        help="Adiciona diretório de busca para copybooks da diretiva COPY",
    )
    diag_group.add_argument(
        "-g",
        "--debug",
        dest="debug_mode",
        action="store_true",
        help="Ativa linhas de depuração COBOL (coluna 7 = 'D') e símbolos DWARF (-g)",
    )
    diag_group.add_argument(
        "-Wall",
        action="store_true",
        help="Habilita todos os avisos (W) e sugestões informativas (I)",
    )
    diag_group.add_argument(
        "-Wno-info",
        action="store_true",
        help="Oculta diagnósticos de nível I (Informational)",
    )
    diag_group.add_argument(
        "-w",
        dest="suppress_warnings",
        action="store_true",
        help="Suprime todos os diagnósticos de nível I (Informational) e W (Warning)",
    )
    diag_group.add_argument(
        "-Werror",
        dest="warnings_as_errors",
        action="store_true",
        help="Trata todos os avisos de nível W (Warning) como erros graves (E)",
    )

    # Extensibilidade de Backends e Inspeção
    ext_group = parser.add_argument_group("Backends Plugáveis e Inspeção do Pipeline")
    ext_group.add_argument(
        "--backend",
        default="llvm",
        metavar="NOME_OU_ARQUIVO.py",
        help="Seleciona o middle-end/back-end (padrão: 'llvm' ou caminho de um plugin .py)",
    )
    ext_group.add_argument(
        "--list-backends",
        action="store_true",
        help="Lista os backends registrados e instruções para acoplar novos geradores",
    )
    ext_group.add_argument(
        "-Xllvm",
        dest="extra_llvm_flags",
        action="append",
        default=[],
        metavar="FLAG",
        help="Repassa uma flag diretamente para o toolchain LLVM (clang/opt/llc)",
    )
    ext_group.add_argument(
        "--dump-tokens",
        action="store_true",
        help="Imprime a tabela de tokens produzida pelo analisador léxico",
    )
    ext_group.add_argument(
        "--dump-ast",
        action="store_true",
        help="Imprime a Árvore Sintática Abstrata (AST) produzida pelo analisador sintático",
    )
    ext_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Exibe detalhadamente cada etapa do pipeline e os comandos invocadas",
    )
    ext_group.add_argument(
        "--version",
        action="version",
        version=f"COBython {VERSION} (Grupo 9 - Compilador COBOL com backend LLVM)",
    )

    return parser


def print_backends_help() -> None:
    print("Backends registrados no COBython:")
    for name, b in BACKEND_REGISTRY.items():
        print(f"  - {name:<10} : {b.description} (extensão padrão: {b.default_extension})")
    print()
    print("Como adicionar um transpilador ou gerador de assembly direto:")
    print("  1. Crie um arquivo Python (ex: 'c_transpiler.py' ou 'x86_asm.py') contendo:")
    print("     - Uma subclasse de `cobython.CodegenBackend`, OU")
    print("     - Uma função `generate(program: ProgramNode, options: CompilerOptions) -> str`.")
    print("  2. Execute: python3 cobython.py arquivo.cob --backend c_transpiler.py -o saida.c")


def main(argv: list[str] | None = None) -> int:
    arg_parser = build_arg_parser()
    args = arg_parser.parse_args(argv)

    if args.list_backends:
        print_backends_help()
        return 0

    if not args.inputs:
        arg_parser.print_usage(sys.stderr)
        print("cobython: erro: nenhum arquivo de entrada especificado.", file=sys.stderr)
        return 1

    options = CompilerOptions(
        input_files=args.inputs,
        output_file=args.output,
        opt_level=args.opt_level or "-O0",
        emit_llvm=args.emit_llvm,
        compile_only=args.compile_only,
        assembly_only=args.assembly_only,
        syntax_only=args.fsyntax_only,
        debug_mode=args.debug_mode,
        include_dirs=args.include_dirs,
        backend_name=args.backend,
        extra_llvm_flags=args.extra_llvm_flags,
        suppress_warnings=args.suppress_warnings,
        no_info=args.Wno_info,
        warnings_as_errors=args.warnings_as_errors,
        dump_tokens=args.dump_tokens,
        dump_ast=args.dump_ast,
        keep_temps=args.save_temps,
        verbose=args.verbose,
    )

    try:
        backend = load_backend(options.backend_name)
    except ValueError as exc:
        print(f"cobython: [S - SEVERE] (DRIVER): {exc}", file=sys.stderr)
        return 1

    overall_ok = True
    for src_file in options.input_files:
        ok = compile_single_file(src_file, options, backend)
        if not ok:
            overall_ok = False

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
