# Integrantes do grupo 9:
#   Thiago Costa    - Nº USP 14609529
#   Oliver Kuramae  - Nº USP 14601326
#   Theo Brito      - Nº USP 13688367

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

from lexer import (
    COMMENT_ENTRY_PARAGRAPHS,
    CompileError,
    Token,
    expand_copybooks,
    make_token_stream,
)

# ==============================================================================
# NÓS DA ÁRVORE SINTÁTICA ABSTRATA (AST)
# ==============================================================================


@dataclass
class ASTNode:
    line: int
    col: int


# --- 3.1 IDENTIFICATION DIVISION ---


@dataclass
class CommentParagraphNode(ASTNode):
    name: str
    entries: list[str] = field(default_factory=list)


@dataclass
class IdentificationDivisionNode(ASTNode):
    program_id: str | None = None
    paragraphs: list[CommentParagraphNode] = field(default_factory=list)


# --- 3.2 ENVIRONMENT DIVISION ---


@dataclass
class ConfigurationSectionNode(ASTNode):
    source_computer: str | None = None
    object_computer: str | None = None
    special_names: list[str] = field(default_factory=list)


@dataclass
class SelectClauseNode:
    clause_type: str
    value: str


@dataclass
class SelectNode(ASTNode):
    file_name: str
    assign_to: str
    clauses: list[SelectClauseNode] = field(default_factory=list)


@dataclass
class InputOutputSectionNode(ASTNode):
    file_control: list[SelectNode] = field(default_factory=list)


@dataclass
class EnvironmentDivisionNode(ASTNode):
    configuration_section: ConfigurationSectionNode | None = None
    input_output_section: InputOutputSectionNode | None = None


# --- 3.3 DATA DIVISION ---


@dataclass
class OccursClauseNode:
    times: int
    indexed_by: list[str] = field(default_factory=list)


@dataclass
class DataItemNode(ASTNode):
    level: int
    name: str
    pic: str | None = None
    value: str | None = None
    occurs: OccursClauseNode | None = None
    children: list[DataItemNode] = field(default_factory=list)


@dataclass
class FileDescriptionNode(ASTNode):
    file_name: str
    clauses: list[str] = field(default_factory=list)
    records: list[DataItemNode] = field(default_factory=list)


@dataclass
class DataDivisionNode(ASTNode):
    file_section: list[FileDescriptionNode] = field(default_factory=list)
    working_storage: list[DataItemNode] = field(default_factory=list)


# --- EXPRESSÕES E CONDIÇÕES ---


@dataclass
class ExprNode(ASTNode):
    pass


@dataclass
class LiteralNode(ExprNode):
    literal_type: str  # NUMBER, ALPHANUMERIC_LITERAL, FIGURATIVE_CONSTANT
    value: str


@dataclass
class IdentifierRef(ExprNode):
    name: str
    qualifiers: list[str] = field(default_factory=list)
    subscripts: list[ExprNode] = field(default_factory=list)


@dataclass
class FunctionCallNode(ExprNode):
    function_name: str
    arguments: list[ExprNode] = field(default_factory=list)


@dataclass
class UnaryExprNode(ExprNode):
    operator: str
    operand: ExprNode


@dataclass
class BinaryExprNode(ExprNode):
    operator: str
    left: ExprNode
    right: ExprNode


@dataclass
class ConditionNode(ASTNode):
    pass


@dataclass
class SimpleConditionNode(ConditionNode):
    operand: ExprNode


@dataclass
class RelationConditionNode(ConditionNode):
    operator: str
    left: ExprNode
    right: ExprNode


@dataclass
class NotConditionNode(ConditionNode):
    operand: ConditionNode


@dataclass
class LogicalConditionNode(ConditionNode):
    operator: str  # AND, OR
    left: ConditionNode
    right: ConditionNode


# --- 3.4 PROCEDURE DIVISION (STATEMENTS, SENTENCES, PARAGRAPHS, SECTIONS) ---


@dataclass
class StatementNode(ASTNode):
    pass


@dataclass
class DisplayStmt(StatementNode):
    operands: list[ExprNode] = field(default_factory=list)
    no_advancing: bool = False


@dataclass
class AcceptStmt(StatementNode):
    target: IdentifierRef | None = None


@dataclass
class MoveStmt(StatementNode):
    source: ExprNode | None = None
    targets: list[IdentifierRef] = field(default_factory=list)


@dataclass
class PerformStmt(StatementNode):
    target: str
    thru: str | None = None
    until: ConditionNode | None = None
    times: ExprNode | None = None


@dataclass
class StopRunStmt(StatementNode):
    pass


@dataclass
class GoToStmt(StatementNode):
    target: str


@dataclass
class IfStmt(StatementNode):
    condition: ConditionNode | None = None
    then_body: list[StatementNode] = field(default_factory=list)
    else_body: list[StatementNode] = field(default_factory=list)
    has_end_if: bool = False


@dataclass
class OpenGroup:
    mode: str
    files: list[str] = field(default_factory=list)


@dataclass
class OpenStmt(StatementNode):
    groups: list[OpenGroup] = field(default_factory=list)


@dataclass
class CloseStmt(StatementNode):
    files: list[str] = field(default_factory=list)


@dataclass
class ReadStmt(StatementNode):
    file_name: str
    is_record: bool = False
    into: IdentifierRef | None = None
    key: IdentifierRef | None = None
    invalid_key: list[StatementNode] = field(default_factory=list)
    not_invalid_key: list[StatementNode] = field(default_factory=list)
    at_end: list[StatementNode] = field(default_factory=list)
    not_at_end: list[StatementNode] = field(default_factory=list)


@dataclass
class WriteStmt(StatementNode):
    record_name: str
    from_id: IdentifierRef | None = None
    invalid_key: list[StatementNode] = field(default_factory=list)
    not_invalid_key: list[StatementNode] = field(default_factory=list)


@dataclass
class StartStmt(StatementNode):
    file_name: str
    key_operator: str | None = None
    key_name: IdentifierRef | None = None
    invalid_key: list[StatementNode] = field(default_factory=list)
    not_invalid_key: list[StatementNode] = field(default_factory=list)


@dataclass
class AddStmt(StatementNode):
    operands: list[ExprNode] = field(default_factory=list)
    to_targets: list[IdentifierRef] = field(default_factory=list)
    giving_targets: list[IdentifierRef] = field(default_factory=list)
    on_size_error: list[StatementNode] = field(default_factory=list)
    not_on_size_error: list[StatementNode] = field(default_factory=list)


@dataclass
class SubtractStmt(StatementNode):
    operands: list[ExprNode] = field(default_factory=list)
    from_targets: list[IdentifierRef] = field(default_factory=list)
    giving_targets: list[IdentifierRef] = field(default_factory=list)
    on_size_error: list[StatementNode] = field(default_factory=list)
    not_on_size_error: list[StatementNode] = field(default_factory=list)


@dataclass
class MultiplyStmt(StatementNode):
    operand: ExprNode | None = None
    by_targets: list[IdentifierRef] = field(default_factory=list)
    giving_targets: list[IdentifierRef] = field(default_factory=list)
    on_size_error: list[StatementNode] = field(default_factory=list)
    not_on_size_error: list[StatementNode] = field(default_factory=list)


@dataclass
class DivideStmt(StatementNode):
    operand: ExprNode | None = None
    mode: str = "BY"  # BY ou INTO
    targets: list[IdentifierRef | ExprNode] = field(default_factory=list)
    giving_targets: list[IdentifierRef] = field(default_factory=list)
    remainder: IdentifierRef | None = None
    on_size_error: list[StatementNode] = field(default_factory=list)
    not_on_size_error: list[StatementNode] = field(default_factory=list)


@dataclass
class ComputeStmt(StatementNode):
    targets: list[IdentifierRef] = field(default_factory=list)
    expression: ExprNode | None = None
    on_size_error: list[StatementNode] = field(default_factory=list)
    not_on_size_error: list[StatementNode] = field(default_factory=list)


@dataclass
class EvaluateWhenBranch(ASTNode):
    condition: ExprNode | str  # ExprNode ou "OTHER"
    body: list[StatementNode] = field(default_factory=list)


@dataclass
class EvaluateStmt(StatementNode):
    subject: ExprNode | None = None
    branches: list[EvaluateWhenBranch] = field(default_factory=list)


@dataclass
class SearchWhenBranch(ASTNode):
    condition: ConditionNode | None = None
    body: list[StatementNode] = field(default_factory=list)


@dataclass
class SearchStmt(StatementNode):
    table: IdentifierRef | None = None
    at_end: list[StatementNode] = field(default_factory=list)
    branches: list[SearchWhenBranch] = field(default_factory=list)


@dataclass
class SetStmt(StatementNode):
    targets: list[IdentifierRef] = field(default_factory=list)
    mode: str = "TO"  # TO, UP BY, DOWN BY
    value: ExprNode | None = None


@dataclass
class InspectStmt(StatementNode):
    target: IdentifierRef | None = None
    mode: str = ""  # TALLYING, REPLACING, CONVERTING
    clauses: list[str] = field(default_factory=list)


@dataclass
class SentenceNode(ASTNode):
    statements: list[StatementNode] = field(default_factory=list)


@dataclass
class ParagraphNode(ASTNode):
    name: str
    sentences: list[SentenceNode] = field(default_factory=list)


@dataclass
class ProcedureSectionNode(ASTNode):
    name: str
    sentences: list[SentenceNode] = field(default_factory=list)
    paragraphs: list[ParagraphNode] = field(default_factory=list)


@dataclass
class ProcedureDivisionNode(ASTNode):
    sentences: list[SentenceNode] = field(default_factory=list)
    paragraphs: list[ParagraphNode] = field(default_factory=list)
    sections: list[ProcedureSectionNode] = field(default_factory=list)


@dataclass
class ProgramNode(ASTNode):
    identification_division: IdentificationDivisionNode | None = None
    environment_division: EnvironmentDivisionNode | None = None
    data_division: DataDivisionNode | None = None
    procedure_division: ProcedureDivisionNode | None = None
    end_program: str | None = None


# ==============================================================================
# CONSTANTES E TABELAS AUXILIARES DO PARSER
# ==============================================================================

DIVISION_NAMES = {"IDENTIFICATION", "ENVIRONMENT", "DATA", "PROCEDURE"}
DIVISION_ORDER = {
    "IDENTIFICATION": 1,
    "ENVIRONMENT": 2,
    "DATA": 3,
    "PROCEDURE": 4,
}

FIGURATIVE_CONSTANTS = {
    "ZERO", "ZEROS", "ZEROES",
    "SPACE", "SPACES",
    "HIGH-VALUE", "HIGH-VALUES",
    "LOW-VALUE", "LOW-VALUES",
    "QUOTE", "QUOTES",
    "ALL",
}

STATEMENT_VERBS = {
    "DISPLAY", "ACCEPT", "MOVE", "PERFORM", "STOP", "GO", "IF",
    "OPEN", "CLOSE", "READ", "WRITE", "START",
    "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "COMPUTE",
    "EVALUATE", "SEARCH", "SET", "INSPECT",
}

SCOPE_DELIMITERS = {
    "ELSE", "END-IF",
    "END-EVALUATE", "WHEN",
    "END-SEARCH", "AT",
    "END-READ", "END-WRITE", "END-START",
    "END-ADD", "END-SUBTRACT", "END-MULTIPLY", "END-DIVIDE", "END-COMPUTE",
    "END-PERFORM",
    "INVALID", "NOT", "ON", "SIZE",
}

IDENTIFIER_TOKEN_TYPES = {"VALID_ID", "ERROR_INVALID_ID", "ERROR_ID_TOO_BIG"}
LITERAL_TOKEN_TYPES = {"NUMBER", "ALPHANUMERIC_LITERAL", "ERROR_UNCLOSED_STRING"}

PIC_VALID_REGEX = re.compile(r"^[+-]?[9XASVZPB0/,.\-+*$CRDB()0-9]+$")


# ==============================================================================
# ANALISADOR SINTÁTICO RECURSIVO DESCENDENTE
# ==============================================================================


class Parser:
    def __init__(self, tokens: list[Token], errors: list[CompileError] | None = None):
        # Filtra diretivas COPY malformadas remanescentes para não causar cascata de erros
        self.tokens: list[Token] = self._clean_tokens(tokens)
        self.errors: list[CompileError] = list(errors) if errors is not None else []
        self.pos: int = 0

    @staticmethod
    def _clean_tokens(tokens: list[Token]) -> list[Token]:
        cleaned: list[Token] = []
        i = 0
        while i < len(tokens):
            if tokens[i].type == "COPY":
                # A diretiva COPY já foi diagnosticada como malformada pelo expand_copybooks
                i += 1
                if i < len(tokens) and tokens[i].type in ("VALID_ID", "ALPHANUMERIC_LITERAL"):
                    i += 1
                if i < len(tokens) and tokens[i].type == "DOT":
                    i += 1
                continue
            cleaned.append(tokens[i])
            i += 1
        return cleaned

    # --- Utilitários de navegação no fluxo de tokens ---

    def current(self) -> Token | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def peek(self, offset: int = 1) -> Token | None:
        idx = self.pos + offset
        if 0 <= idx < len(self.tokens):
            return self.tokens[idx]
        return None

    def at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def last_position(self) -> tuple[int, int]:
        if self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            return tok.line, tok.col
        if self.tokens:
            tok = self.tokens[-1]
            return tok.line, tok.col + len(tok.value)
        return 1, 1

    def error(self, message: str, token: Token | None = None, severity: str = "E") -> None:
        if token is not None:
            line, col = token.line, token.col
        else:
            line, col = self.last_position()
        self.errors.append(CompileError(severity, line, col, message))

    def match_word(self, *words: str) -> bool:
        tok = self.current()
        if tok is None:
            return False
        if tok.type in ("ALPHANUMERIC_LITERAL", "ERROR_UNCLOSED_STRING", "COMMENT_ENTRY"):
            return False
        return tok.value in words or tok.type in words

    def consume_word(self, word: str, error_msg: str | None = None) -> Token | None:
        if self.match_word(word):
            return self.advance()
        if error_msg:
            self.error(error_msg, self.current())
        return None

    def consume_dot(self, context: str = "declaração") -> Token | None:
        tok = self.current()
        if tok is not None and tok.type == "DOT":
            return self.advance()
        self.error(f"Esperado ponto final ('.') após {context}", tok)
        return None

    def skip_optional_separators(self) -> None:
        # Vírgula e ponto e vírgula fora de literais funcionam como separadores opcionais em COBOL
        while not self.at_end() and self.current().type in ("COMMA", "SEMICOLON"):
            self.advance()

    def is_identifier_token(self, tok: Token | None = None) -> bool:
        if tok is None:
            tok = self.current()
        if tok is None:
            return False
        if tok.type in IDENTIFIER_TOKEN_TYPES:
            return True
        return False

    def consume_identifier(self, context: str = "identificador") -> Token | None:
        tok = self.current()
        if tok is not None and self.is_identifier_token(tok):
            return self.advance()
        self.error(f"Esperado {context}, encontrado '{tok.value if tok else 'EOF'}'", tok)
        return None

    def is_division_header(self, offset: int = 0) -> bool:
        t1 = self.peek(offset)
        t2 = self.peek(offset + 1)
        return (
            t1 is not None
            and t2 is not None
            and t1.value in DIVISION_NAMES
            and t2.type == "DIVISION"
        )

    def is_end_program(self, offset: int = 0) -> bool:
        t1 = self.peek(offset)
        t2 = self.peek(offset + 1)
        return (
            t1 is not None
            and t2 is not None
            and t1.value == "END"
            and t2.value == "PROGRAM"
        )

    def is_section_header(self, offset: int = 0) -> bool:
        t1 = self.peek(offset)
        t2 = self.peek(offset + 1)
        if t1 is None or t2 is None:
            return False
        return (
            t1.type not in ("ALPHANUMERIC_LITERAL", "ERROR_UNCLOSED_STRING", "DOT")
            and t2.type == "SECTION"
        )

    def is_procedure_paragraph_header(self) -> bool:
        t1 = self.current()
        t2 = self.peek(1)
        if t1 is None or t2 is None:
            return False
        if t1.area != "AREA_A":
            return False
        if self.is_division_header() or self.is_section_header() or self.is_end_program():
            return False
        if t1.value in STATEMENT_VERBS or t1.value in SCOPE_DELIMITERS:
            return False
        return (self.is_identifier_token(t1) or t1.type == "NUMBER") and t2.type == "DOT"

    def recover_to_dot_or_header(self) -> None:
        while not self.at_end():
            tok = self.current()
            if tok.type == "DOT":
                self.advance()
                return
            if self.is_division_header() or self.is_section_header() or self.is_end_program():
                return
            if self.is_procedure_paragraph_header():
                return
            self.advance()

    # ==========================================================================
    # 3. PROGRAMA PRINCIPAL E HIERARQUIA DAS QUATRO DIVISÕES
    # <Program> ::= <IdentificationDivision> [<EnvironmentDivision>]
    #               [<DataDivision>] <ProcedureDivision>
    # ==========================================================================

    def parse_program(self) -> ProgramNode:
        start_line, start_col = self.last_position()
        program = ProgramNode(line=start_line, col=start_col)

        if self.at_end():
            self.error("Arquivo fonte vazio: esperado IDENTIFICATION DIVISION")
            return program

        last_division_rank = 0
        seen_divisions: set[str] = set()

        while not self.at_end() and not self.is_end_program():
            # Avança até o próximo cabeçalho de divisão caso haja tokens órfãos no início
            if not self.is_division_header():
                tok = self.current()
                self.error(
                    f"Token inesperado fora de divisão: '{tok.value}' (esperado cabeçalho de DIVISION)",
                    tok,
                )
                while not self.at_end() and not self.is_division_header() and not self.is_end_program():
                    self.advance()
                continue

            div_token = self.current()
            div_name = div_token.value
            div_rank = DIVISION_ORDER[div_name]

            # Verifica a Área A do cabeçalho da divisão (Seção 2.1)
            if div_token.area != "AREA_A":
                self.error(
                    f"Cabeçalho '{div_name} DIVISION' deve iniciar na Área A (colunas 8-11)",
                    div_token,
                    severity="W",
                )

            # Verifica duplicidade e ordem estrita de derivação (Seção 3)
            if div_name in seen_divisions:
                self.error(f"Divisão '{div_name} DIVISION' declarada mais de uma vez", div_token)
            elif div_rank < last_division_rank:
                self.error(
                    f"'{div_name} DIVISION' fora da ordem obrigatória "
                    f"(IDENTIFICATION -> ENVIRONMENT -> DATA -> PROCEDURE)",
                    div_token,
                )
            elif last_division_rank == 0 and div_name != "IDENTIFICATION":
                self.error(
                    f"O programa deve iniciar obrigatoriamente com 'IDENTIFICATION DIVISION' "
                    f"(encontrado '{div_name} DIVISION')",
                    div_token,
                )

            seen_divisions.add(div_name)
            if div_rank > last_division_rank:
                last_division_rank = div_rank

            if div_name == "IDENTIFICATION":
                program.identification_division = self.parse_identification_division()
            elif div_name == "ENVIRONMENT":
                program.environment_division = self.parse_environment_division()
            elif div_name == "DATA":
                program.data_division = self.parse_data_division()
            elif div_name == "PROCEDURE":
                program.procedure_division = self.parse_procedure_division()

        # Verifica divisões obrigatórias ausentes
        if "IDENTIFICATION" not in seen_divisions and program.identification_division is None:
            self.error("Divisão obrigatória 'IDENTIFICATION DIVISION' ausente no programa")
        if "PROCEDURE" not in seen_divisions and program.procedure_division is None:
            self.error("Divisão obrigatória 'PROCEDURE DIVISION' ausente no programa")

        # Cláusula opcional de encerramento: END PROGRAM [NomeDoPrograma].
        if self.is_end_program():
            end_tok = self.advance()  # END
            if end_tok.area != "AREA_A":
                self.error("Cabeçalho 'END PROGRAM' deve iniciar na Área A", end_tok, severity="W")
            self.advance()  # PROGRAM
            prog_name_tok = self.consume_identifier("nome do programa após END PROGRAM")
            if prog_name_tok is not None:
                program.end_program = prog_name_tok.value
                # Verifica consistência sintática com o PROGRAM-ID se ambos existirem
                if (
                    program.identification_division
                    and program.identification_division.program_id
                    and program.identification_division.program_id != prog_name_tok.value
                ):
                    self.error(
                        f"Nome em END PROGRAM ('{prog_name_tok.value}') difere do "
                        f"PROGRAM-ID ('{program.identification_division.program_id}')",
                        prog_name_tok,
                    )
            self.consume_dot("END PROGRAM")

        # Tokens residuais após END PROGRAM
        if not self.at_end():
            tok = self.current()
            self.error(f"Tokens inesperados após o fim do programa: '{tok.value}'", tok)

        return program

    # ==========================================================================
    # 3.1 IDENTIFICATION DIVISION
    # IDENTIFICATION DIVISION.
    # PROGRAM-ID. [NomeDoPrograma].
    # ==========================================================================

    def parse_identification_division(self) -> IdentificationDivisionNode:
        start_tok = self.advance()  # IDENTIFICATION
        self.consume_word("DIVISION", "Esperado 'DIVISION' após 'IDENTIFICATION'")
        self.consume_dot("IDENTIFICATION DIVISION")

        node = IdentificationDivisionNode(line=start_tok.line, col=start_tok.col)

        # Parágrafo obrigatório: PROGRAM-ID. [NomeDoPrograma].
        if self.match_word("PROGRAM-ID"):
            pid_tok = self.advance()
            if pid_tok.area != "AREA_A":
                self.error("Parágrafo 'PROGRAM-ID' deve iniciar na Área A", pid_tok, severity="W")
            self.consume_dot("PROGRAM-ID")
            name_tok = self.consume_identifier("nome do programa após PROGRAM-ID.")
            if name_tok is not None:
                node.program_id = name_tok.value
            self.consume_dot("nome do programa em PROGRAM-ID")
        else:
            self.error(
                "Parágrafo obrigatório 'PROGRAM-ID.' ausente no início da IDENTIFICATION DIVISION",
                self.current() or start_tok,
            )

        # Parágrafos informativos opcionais (AUTHOR, INSTALLATION, DATE-WRITTEN, etc.)
        while not self.at_end() and not self.is_division_header() and not self.is_end_program():
            tok = self.current()
            if tok.type in COMMENT_ENTRY_PARAGRAPHS or tok.value in COMMENT_ENTRY_PARAGRAPHS:
                p_tok = self.advance()
                if p_tok.area != "AREA_A":
                    self.error(f"Parágrafo '{p_tok.value}' deve iniciar na Área A", p_tok, severity="W")
                self.consume_dot(p_tok.value)
                entries: list[str] = []
                while not self.at_end() and self.current().type == "COMMENT_ENTRY":
                    entries.append(self.advance().value)
                node.paragraphs.append(
                    CommentParagraphNode(line=p_tok.line, col=p_tok.col, name=p_tok.value, entries=entries)
                )
            elif self.match_word("PROGRAM-ID"):
                self.error("Parágrafo 'PROGRAM-ID' duplicado na IDENTIFICATION DIVISION", tok)
                self.recover_to_dot_or_header()
            else:
                self.error(f"Elemento inválido na IDENTIFICATION DIVISION: '{tok.value}'", tok)
                self.recover_to_dot_or_header()

        return node

    # ==========================================================================
    # 3.2 ENVIRONMENT DIVISION
    # ENVIRONMENT DIVISION.
    # CONFIGURATION SECTION.
    #   SOURCE-COMPUTER. [Nome].
    #   OBJECT-COMPUTER. [Nome].
    # INPUT-OUTPUT SECTION.
    #   FILE-CONTROL.
    #     SELECT [NomeArquivo] ASSIGN TO [DestinoFisico].
    # ==========================================================================

    def parse_environment_division(self) -> EnvironmentDivisionNode:
        start_tok = self.advance()  # ENVIRONMENT
        self.consume_word("DIVISION", "Esperado 'DIVISION' após 'ENVIRONMENT'")
        self.consume_dot("ENVIRONMENT DIVISION")

        node = EnvironmentDivisionNode(line=start_tok.line, col=start_tok.col)

        while not self.at_end() and not self.is_division_header() and not self.is_end_program():
            tok = self.current()
            if self.match_word("CONFIGURATION"):
                node.configuration_section = self.parse_configuration_section()
            elif self.match_word("INPUT-OUTPUT"):
                node.input_output_section = self.parse_input_output_section()
            else:
                self.error(
                    f"Seção inválida na ENVIRONMENT DIVISION: '{tok.value}' "
                    f"(esperado CONFIGURATION SECTION ou INPUT-OUTPUT SECTION)",
                    tok,
                )
                self.recover_to_dot_or_header()

        return node

    def parse_configuration_section(self) -> ConfigurationSectionNode:
        start_tok = self.advance()  # CONFIGURATION
        if start_tok.area != "AREA_A":
            self.error("Cabeçalho 'CONFIGURATION SECTION' deve iniciar na Área A", start_tok, severity="W")
        self.consume_word("SECTION", "Esperado 'SECTION' após 'CONFIGURATION'")
        self.consume_dot("CONFIGURATION SECTION")

        sec = ConfigurationSectionNode(line=start_tok.line, col=start_tok.col)

        while (
            not self.at_end()
            and not self.is_division_header()
            and not self.is_section_header()
            and not self.is_end_program()
        ):
            tok = self.current()
            if self.match_word("SOURCE-COMPUTER"):
                self.advance()
                self.consume_dot("SOURCE-COMPUTER")
                parts: list[str] = []
                while not self.at_end() and self.current().type != "DOT":
                    if self.is_division_header() or self.is_section_header():
                        break
                    parts.append(self.advance().value)
                if parts:
                    sec.source_computer = " ".join(parts)
                    self.consume_dot("nome em SOURCE-COMPUTER")
            elif self.match_word("OBJECT-COMPUTER"):
                self.advance()
                self.consume_dot("OBJECT-COMPUTER")
                parts = []
                while not self.at_end() and self.current().type != "DOT":
                    if self.is_division_header() or self.is_section_header():
                        break
                    parts.append(self.advance().value)
                if parts:
                    sec.object_computer = " ".join(parts)
                    self.consume_dot("nome em OBJECT-COMPUTER")
            elif self.match_word("SPECIAL-NAMES"):
                self.advance()
                self.consume_dot("SPECIAL-NAMES")
                while not self.at_end() and self.current().type != "DOT":
                    if self.is_division_header() or self.is_section_header():
                        break
                    sec.special_names.append(self.advance().value)
                if not self.at_end() and self.current().type == "DOT":
                    self.advance()
            else:
                self.error(f"Parágrafo inválido em CONFIGURATION SECTION: '{tok.value}'", tok)
                self.recover_to_dot_or_header()

        return sec

    def parse_input_output_section(self) -> InputOutputSectionNode:
        start_tok = self.advance()  # INPUT-OUTPUT
        if start_tok.area != "AREA_A":
            self.error("Cabeçalho 'INPUT-OUTPUT SECTION' deve iniciar na Área A", start_tok, severity="W")
        self.consume_word("SECTION", "Esperado 'SECTION' após 'INPUT-OUTPUT'")
        self.consume_dot("INPUT-OUTPUT SECTION")

        sec = InputOutputSectionNode(line=start_tok.line, col=start_tok.col)

        while (
            not self.at_end()
            and not self.is_division_header()
            and not self.is_section_header()
            and not self.is_end_program()
        ):
            tok = self.current()
            if self.match_word("FILE-CONTROL"):
                fc_tok = self.advance()
                if fc_tok.area != "AREA_A":
                    self.error("Parágrafo 'FILE-CONTROL' deve iniciar na Área A", fc_tok, severity="W")
                self.consume_dot("FILE-CONTROL")
                while not self.at_end() and self.match_word("SELECT"):
                    sec.file_control.append(self.parse_select_entry())
            elif self.match_word("SELECT"):
                self.error("Cláusula SELECT deve estar precedida pelo parágrafo 'FILE-CONTROL.'", tok)
                sec.file_control.append(self.parse_select_entry())
            else:
                self.error(f"Elemento inválido em INPUT-OUTPUT SECTION: '{tok.value}'", tok)
                self.recover_to_dot_or_header()

        return sec

    def parse_select_entry(self) -> SelectNode:
        sel_tok = self.advance()  # SELECT
        file_tok = self.consume_identifier("nome do arquivo após SELECT")
        file_name = file_tok.value if file_tok else "<DESCONHECIDO>"

        assign_to = ""
        clauses: list[SelectClauseNode] = []

        if self.match_word("ASSIGN"):
            self.advance()  # ASSIGN
            if self.match_word("TO"):
                self.advance()  # TO
            dest_tok = self.current()
            if dest_tok is not None and (
                self.is_identifier_token(dest_tok)
                or dest_tok.type in ("ALPHANUMERIC_LITERAL", "ERROR_UNCLOSED_STRING")
            ):
                assign_to = self.advance().value
            else:
                self.error("Esperado destino físico (identificador ou literal) após ASSIGN TO", dest_tok)
        else:
            self.error(f"Esperado 'ASSIGN TO' na declaração SELECT do arquivo '{file_name}'", self.current())

        # Cláusulas adicionais opcionais do SELECT (ORGANIZATION, ACCESS MODE, RECORD KEY, FILE STATUS)
        while not self.at_end() and self.current().type != "DOT":
            if self.is_division_header() or self.is_section_header() or self.match_word("SELECT"):
                break
            tok = self.current()
            if self.match_word("ORGANIZATION"):
                self.advance()
                if self.match_word("IS"):
                    self.advance()
                if self.match_word("LINE") and self.peek(1) and self.peek(1).value == "SEQUENTIAL":
                    self.advance()
                    self.advance()
                    clauses.append(SelectClauseNode("ORGANIZATION", "LINE SEQUENTIAL"))
                elif not self.at_end() and self.current().type != "DOT":
                    clauses.append(SelectClauseNode("ORGANIZATION", self.advance().value))
            elif self.match_word("ACCESS"):
                self.advance()
                if self.match_word("MODE"):
                    self.advance()
                if self.match_word("IS"):
                    self.advance()
                if not self.at_end() and self.current().type != "DOT":
                    clauses.append(SelectClauseNode("ACCESS_MODE", self.advance().value))
            elif self.match_word("RECORD"):
                self.advance()
                if self.match_word("KEY"):
                    self.advance()
                if self.match_word("IS"):
                    self.advance()
                key_tok = self.consume_identifier("chave em RECORD KEY")
                if key_tok:
                    clauses.append(SelectClauseNode("RECORD_KEY", key_tok.value))
            elif self.match_word("ALTERNATE"):
                self.advance()
                if self.match_word("RECORD"):
                    self.advance()
                if self.match_word("KEY"):
                    self.advance()
                if self.match_word("IS"):
                    self.advance()
                key_tok = self.consume_identifier("chave em ALTERNATE RECORD KEY")
                if key_tok:
                    clauses.append(SelectClauseNode("ALTERNATE_RECORD_KEY", key_tok.value))
            elif self.match_word("FILE", "STATUS"):
                if self.match_word("FILE"):
                    self.advance()
                self.consume_word("STATUS", "Esperado 'STATUS' após 'FILE'")
                if self.match_word("IS"):
                    self.advance()
                st_tok = self.consume_identifier("variável em FILE STATUS")
                if st_tok:
                    clauses.append(SelectClauseNode("FILE_STATUS", st_tok.value))
            else:
                self.error(f"Cláusula desconhecida em SELECT: '{tok.value}'", tok)
                self.advance()

        self.consume_dot(f"SELECT {file_name}")
        return SelectNode(
            line=sel_tok.line,
            col=sel_tok.col,
            file_name=file_name,
            assign_to=assign_to,
            clauses=clauses,
        )

    # ==========================================================================
    # 3.3 DATA DIVISION
    # Níveis: 01, 02-49, 77, 88
    # Cláusulas: PICTURE/PIC, VALUE/VALUES, OCCURS
    # ==========================================================================

    def parse_data_division(self) -> DataDivisionNode:
        start_tok = self.advance()  # DATA
        self.consume_word("DIVISION", "Esperado 'DIVISION' após 'DATA'")
        self.consume_dot("DATA DIVISION")

        node = DataDivisionNode(line=start_tok.line, col=start_tok.col)

        while not self.at_end() and not self.is_division_header() and not self.is_end_program():
            tok = self.current()
            if self.match_word("FILE") and self.peek(1) and self.peek(1).type == "SECTION":
                f_tok = self.advance()  # FILE
                if f_tok.area != "AREA_A":
                    self.error("Cabeçalho 'FILE SECTION' deve iniciar na Área A", f_tok, severity="W")
                self.advance()  # SECTION
                self.consume_dot("FILE SECTION")
                node.file_section.extend(self.parse_file_section_entries())
            elif self.match_word("WORKING-STORAGE") and self.peek(1) and self.peek(1).type == "SECTION":
                ws_tok = self.advance()  # WORKING-STORAGE
                if ws_tok.area != "AREA_A":
                    self.error(
                        "Cabeçalho 'WORKING-STORAGE SECTION' deve iniciar na Área A",
                        ws_tok,
                        severity="W",
                    )
                self.advance()  # SECTION
                self.consume_dot("WORKING-STORAGE SECTION")
                node.working_storage.extend(self.parse_data_items_hierarchy())
            elif tok.type == "NUMBER":
                # Permite declarações de dados diretamente na DATA DIVISION (ex: test.cob)
                node.working_storage.extend(self.parse_data_items_hierarchy())
            else:
                self.error(f"Elemento inesperado na DATA DIVISION: '{tok.value}'", tok)
                self.recover_to_dot_or_header()

        return node

    def parse_file_section_entries(self) -> list[FileDescriptionNode]:
        fds: list[FileDescriptionNode] = []
        while (
            not self.at_end()
            and not self.is_division_header()
            and not self.is_section_header()
            and not self.is_end_program()
        ):
            tok = self.current()
            if self.match_word("FD"):
                fd_tok = self.advance()  # FD
                if fd_tok.area != "AREA_A":
                    self.error("Descritor 'FD' deve iniciar na Área A", fd_tok, severity="W")
                file_tok = self.consume_identifier("nome do arquivo após FD")
                file_name = file_tok.value if file_tok else "<DESCONHECIDO>"

                clauses: list[str] = []
                while not self.at_end() and self.current().type != "DOT":
                    if self.is_division_header() or self.is_section_header():
                        break
                    if self.match_word("BLOCK"):
                        self.advance()
                        if self.match_word("CONTAINS"):
                            self.advance()
                        num_tok = self.current()
                        num_val = self.advance().value if num_tok and num_tok.type == "NUMBER" else "?"
                        unit = ""
                        if self.match_word("RECORDS", "RECORD", "CHARACTERS", "CHARACTER"):
                            unit = self.advance().value
                        clauses.append(f"BLOCK CONTAINS {num_val} {unit}".strip())
                    elif self.match_word("RECORD"):
                        self.advance()
                        if self.match_word("CONTAINS"):
                            self.advance()
                        num_tok = self.current()
                        num_val = self.advance().value if num_tok and num_tok.type == "NUMBER" else "?"
                        unit = ""
                        if self.match_word("CHARACTERS", "CHARACTER"):
                            unit = self.advance().value
                        clauses.append(f"RECORD CONTAINS {num_val} {unit}".strip())
                    else:
                        clauses.append(self.advance().value)

                self.consume_dot(f"FD {file_name}")
                records = self.parse_data_items_hierarchy(stop_on_fd=True)
                fds.append(
                    FileDescriptionNode(
                        line=fd_tok.line,
                        col=fd_tok.col,
                        file_name=file_name,
                        clauses=clauses,
                        records=records,
                    )
                )
            else:
                self.error(f"Esperado descritor 'FD' na FILE SECTION, encontrado '{tok.value}'", tok)
                self.recover_to_dot_or_header()
        return fds

    def parse_data_items_hierarchy(self, stop_on_fd: bool = False) -> list[DataItemNode]:
        flat_items: list[DataItemNode] = []

        while (
            not self.at_end()
            and not self.is_division_header()
            and not self.is_section_header()
            and not self.is_end_program()
        ):
            if stop_on_fd and self.match_word("FD"):
                break
            tok = self.current()
            if tok.type != "NUMBER":
                self.error(
                    f"Esperado número de nível (01-49, 77, 88) na DATA DIVISION, encontrado '{tok.value}'",
                    tok,
                )
                self.recover_to_dot_or_header()
                continue
            item = self.parse_single_data_item()
            if item is not None:
                flat_items.append(item)

        # Monta a árvore hierárquica de registros/grupos e valida itens elementares
        roots: list[DataItemNode] = []
        stack: list[DataItemNode] = []

        for item in flat_items:
            lvl = item.level
            if lvl == 77:
                stack.clear()
                roots.append(item)
            elif lvl == 1:
                stack = [item]
                roots.append(item)
            elif lvl == 88:
                if stack:
                    stack[-1].children.append(item)
                else:
                    self.error(
                        f"Condition-name de nível 88 ('{item.name}') sem variável associada",
                        Token(item.line, item.col, "AREA_B", "NUMBER", "88"),
                    )
                    roots.append(item)
            elif 2 <= lvl <= 49:
                while stack and stack[-1].level >= lvl:
                    stack.pop()
                if not stack:
                    self.error(
                        f"Item de nível {lvl:02d} ('{item.name}') deve estar subordinado a um registro de nível 01",
                        Token(item.line, item.col, "AREA_B", "NUMBER", f"{lvl:02d}"),
                    )
                    roots.append(item)
                    stack.append(item)
                else:
                    stack[-1].children.append(item)
                    stack.append(item)
            else:
                roots.append(item)

        # Valida cláusulas mandatórias por item elementar (Seção 3.3)
        self._validate_data_items_tree(roots)
        return roots

    def _validate_data_items_tree(self, items: list[DataItemNode]) -> None:
        for item in items:
            non_88_children = [c for c in item.children if c.level != 88]
            if item.level == 77:
                if item.pic is None:
                    self.error(
                        f"Item elementar de nível 77 ('{item.name}') exige cláusula PIC/PICTURE",
                        Token(item.line, item.col, "AREA_A", "NUMBER", "77"),
                    )
            elif item.level == 88:
                if item.value is None:
                    self.error(
                        f"Item de condição de nível 88 ('{item.name}') exige cláusula VALUE",
                        Token(item.line, item.col, "AREA_B", "NUMBER", "88"),
                    )
            elif 1 <= item.level <= 49:
                if non_88_children:
                    # Item de grupo: não deve ter cláusula PIC
                    if item.pic is not None:
                        self.error(
                            f"Item de grupo '{item.name}' (nível {item.level:02d}) possui subcampos e não pode ter PIC",
                            Token(item.line, item.col, "AREA_A", "NUMBER", f"{item.level:02d}"),
                        )
                    self._validate_data_items_tree(item.children)
                else:
                    # Item elementar (folha): exige PIC/PICTURE
                    if item.pic is None:
                        self.error(
                            f"Item elementar '{item.name}' (nível {item.level:02d}) exige cláusula PIC/PICTURE",
                            Token(item.line, item.col, "AREA_B", "NUMBER", f"{item.level:02d}"),
                        )
                    if item.children:
                        self._validate_data_items_tree(item.children)

    def parse_single_data_item(self) -> DataItemNode | None:
        lvl_tok = self.advance()  # NUMBER
        try:
            level = int(lvl_tok.value)
        except ValueError:
            self.error(f"Nível de estrutura inválido: '{lvl_tok.value}'", lvl_tok)
            self.recover_to_dot_or_header()
            return None

        if not (1 <= level <= 49 or level in (77, 88)):
            self.error(
                f"Nível de estrutura '{lvl_tok.value}' inválido na DATA DIVISION (permitidos: 01-49, 77, 88)",
                lvl_tok,
            )

        # Verifica Área A para 01 e 77 (Seção 2.1)
        if level in (1, 77) and lvl_tok.area != "AREA_A":
            self.error(
                f"Nível {lvl_tok.value} deveria iniciar na Área A (colunas 8-11)",
                lvl_tok,
                severity="W",
            )

        # Nome do item de dados (ou FILLER)
        name_tok = self.consume_identifier(f"nome da variável após nível {lvl_tok.value}")
        name = name_tok.value if name_tok else "<ANONIMO>"

        pic_str: str | None = None
        val_str: str | None = None
        occurs_node: OccursClauseNode | None = None

        while not self.at_end() and self.current().type != "DOT":
            if self.is_division_header() or self.is_section_header():
                break
            tok = self.current()

            if self.match_word("PIC", "PICTURE"):
                self.advance()  # PIC / PICTURE
                if self.match_word("IS"):
                    self.advance()  # IS
                pic_tok = self.current()
                if pic_tok is not None and pic_tok.type == "PIC_STRING":
                    pic_str = self.advance().value
                    if not PIC_VALID_REGEX.match(pic_str):
                        self.error(f"Máscara PICTURE inválida: '{pic_str}'", pic_tok)
                else:
                    self.error(f"Esperada máscara de tipo após PIC/PICTURE em '{name}'", pic_tok)
            elif self.match_word("VALUE", "VALUES"):
                self.advance()  # VALUE / VALUES
                if self.match_word("IS", "ARE"):
                    self.advance()
                val_tok = self.current()
                if val_tok is not None and (
                    val_tok.type in LITERAL_TOKEN_TYPES
                    or val_tok.value in FIGURATIVE_CONSTANTS
                    or self.is_identifier_token(val_tok)
                ):
                    val_str = self.advance().value
                else:
                    self.error(f"Esperado literal ou constante figurativa após VALUE em '{name}'", val_tok)
            elif self.match_word("OCCURS"):
                self.advance()  # OCCURS
                num_tok = self.current()
                times = 0
                if num_tok is not None and num_tok.type == "NUMBER":
                    try:
                        times = int(self.advance().value)
                    except ValueError:
                        self.error(f"Quantidade inteira inválida em OCCURS: '{num_tok.value}'", num_tok)
                else:
                    self.error("Esperado número inteiro após cláusula OCCURS", num_tok)
                if self.match_word("TIMES"):
                    self.advance()
                indexed_by: list[str] = []
                if self.match_word("INDEXED"):
                    self.advance()
                    if self.match_word("BY"):
                        self.advance()
                    while not self.at_end() and self.is_identifier_token(self.current()):
                        if self.match_word("PIC", "PICTURE", "VALUE", "VALUES", "OCCURS"):
                            break
                        indexed_by.append(self.advance().value)
                        self.skip_optional_separators()
                occurs_node = OccursClauseNode(times=times, indexed_by=indexed_by)
            else:
                self.error(f"Cláusula desconhecida na declaração de dados de '{name}': '{tok.value}'", tok)
                self.advance()

        self.consume_dot(f"declaração de '{name}'")
        return DataItemNode(
            line=lvl_tok.line,
            col=lvl_tok.col,
            level=level,
            name=name,
            pic=pic_str,
            value=val_str,
            occurs=occurs_node,
        )

    # ==========================================================================
    # 3.4 PROCEDURE DIVISION
    # Dividida em SECTIONS (opcionais), PARAGRAPHS, contendo SENTENCES
    # compostas por STATEMENTS, obrigatoriamente finalizadas por ponto (.).
    # ==========================================================================

    def parse_procedure_division(self) -> ProcedureDivisionNode:
        start_tok = self.advance()  # PROCEDURE
        self.consume_word("DIVISION", "Esperado 'DIVISION' após 'PROCEDURE'")
        self.consume_dot("PROCEDURE DIVISION")

        node = ProcedureDivisionNode(line=start_tok.line, col=start_tok.col)

        current_section: ProcedureSectionNode | None = None
        current_paragraph: ParagraphNode | None = None

        while not self.at_end() and not self.is_division_header() and not self.is_end_program():
            # 1. Cabeçalho de SECTION: <Nome> SECTION.
            if self.is_section_header():
                sec_tok = self.advance()
                if sec_tok.area != "AREA_A":
                    self.error(f"Seção '{sec_tok.value} SECTION' deve iniciar na Área A", sec_tok, severity="W")
                self.advance()  # SECTION
                self.consume_dot(f"seção {sec_tok.value}")
                current_section = ProcedureSectionNode(
                    line=sec_tok.line, col=sec_tok.col, name=sec_tok.value
                )
                node.sections.append(current_section)
                current_paragraph = None
                continue

            # 2. Cabeçalho de PARAGRAPH: <Nome>. (na Área A)
            if self.is_procedure_paragraph_header():
                p_tok = self.advance()
                self.advance()  # DOT
                current_paragraph = ParagraphNode(line=p_tok.line, col=p_tok.col, name=p_tok.value)
                if current_section is not None:
                    current_section.paragraphs.append(current_paragraph)
                else:
                    node.paragraphs.append(current_paragraph)
                continue

            # 3. SENTENCE (uma ou mais instruções encerradas por ponto final '.')
            sentence = self.parse_sentence()
            if sentence is not None and sentence.statements:
                if current_paragraph is not None:
                    current_paragraph.sentences.append(sentence)
                elif current_section is not None:
                    current_section.sentences.append(sentence)
                else:
                    node.sentences.append(sentence)

        return node

    def parse_sentence(self) -> SentenceNode | None:
        tok = self.current()
        if tok is None:
            return None

        # Ponto isolado vazio
        if tok.type == "DOT":
            self.advance()
            return None

        sentence = SentenceNode(line=tok.line, col=tok.col)

        while not self.at_end():
            cur = self.current()
            if cur.type == "DOT":
                self.advance()
                return sentence

            if (
                self.is_division_header()
                or self.is_section_header()
                or self.is_procedure_paragraph_header()
                or self.is_end_program()
            ):
                self.error(
                    f"Sentença iniciada na linha {sentence.line} não foi finalizada por ponto final ('.')",
                    cur,
                )
                return sentence

            stmt = self.parse_statement()
            if stmt is not None:
                sentence.statements.append(stmt)
            else:
                # Falha ao analisar instrução: recupera até o próximo ponto ou cabeçalho
                self.recover_to_dot_or_header()
                return sentence

        # Fim do arquivo atingido sem ponto final na sentença
        self.error(
            f"Sentença iniciada na linha {sentence.line} não foi finalizada por ponto final ('.') antes do fim do arquivo"
        )
        return sentence

    def is_statement_boundary(self) -> bool:
        if self.at_end():
            return True
        tok = self.current()
        if tok.type == "DOT":
            return True
        if tok.value in STATEMENT_VERBS or tok.value in SCOPE_DELIMITERS:
            return True
        if (
            self.is_division_header()
            or self.is_section_header()
            or self.is_procedure_paragraph_header()
            or self.is_end_program()
        ):
            return True
        return False

    def parse_statement_list_until(self, *stop_words: str) -> list[StatementNode]:
        stmts: list[StatementNode] = []
        while not self.at_end():
            tok = self.current()
            if tok.type == "DOT":
                break
            if self.match_word(*stop_words):
                break
            # Caso especial: NOT INVALID KEY / NOT AT END / NOT ON SIZE ERROR
            if tok.value == "NOT" and self.peek(1) and self.peek(1).value in ("INVALID", "AT", "ON", "SIZE"):
                if "NOT" in stop_words:
                    break
            if (
                self.is_division_header()
                or self.is_section_header()
                or self.is_procedure_paragraph_header()
                or self.is_end_program()
            ):
                break
            stmt = self.parse_statement()
            if stmt is not None:
                stmts.append(stmt)
            else:
                break
        return stmts

    def parse_statement(self) -> StatementNode | None:
        tok = self.current()
        if tok is None:
            return None

        if tok.area == "AREA_A" and tok.value in STATEMENT_VERBS:
            self.error(
                f"Comando '{tok.value}' deveria iniciar na Área B (colunas 12-72)",
                tok,
                severity="W",
            )

        verb = tok.value
        match verb:
            case "DISPLAY":
                return self.parse_display_stmt()
            case "ACCEPT":
                return self.parse_accept_stmt()
            case "MOVE":
                return self.parse_move_stmt()
            case "PERFORM":
                return self.parse_perform_stmt()
            case "STOP":
                return self.parse_stop_stmt()
            case "GO":
                return self.parse_goto_stmt()
            case "IF":
                return self.parse_if_stmt()
            case "OPEN":
                return self.parse_open_stmt()
            case "CLOSE":
                return self.parse_close_stmt()
            case "READ":
                return self.parse_read_stmt()
            case "WRITE":
                return self.parse_write_stmt()
            case "START":
                return self.parse_start_stmt()
            case "ADD":
                return self.parse_add_stmt()
            case "SUBTRACT":
                return self.parse_subtract_stmt()
            case "MULTIPLY":
                return self.parse_multiply_stmt()
            case "DIVIDE":
                return self.parse_divide_stmt()
            case "COMPUTE":
                return self.parse_compute_stmt()
            case "EVALUATE":
                return self.parse_evaluate_stmt()
            case "SEARCH":
                return self.parse_search_stmt()
            case "SET":
                return self.parse_set_stmt()
            case "INSPECT":
                return self.parse_inspect_stmt()
            case _:
                self.error(f"Instrução inválida ou desconhecida na PROCEDURE DIVISION: '{tok.value}'", tok)
                return None

    # --- Instruções individuais ---

    def parse_display_stmt(self) -> DisplayStmt:
        start_tok = self.advance()  # DISPLAY
        operands: list[ExprNode] = []
        no_advancing = False

        while not self.at_end() and not self.is_statement_boundary():
            if self.match_word("WITH", "NO", "ADVANCING"):
                if self.match_word("WITH"):
                    self.advance()
                if self.match_word("NO"):
                    self.advance()
                self.consume_word("ADVANCING", "Esperado 'ADVANCING' após 'WITH NO'")
                no_advancing = True
                break
            op = self.parse_operand()
            if op is None:
                break
            operands.append(op)
            self.skip_optional_separators()

        if not operands:
            self.error("Instrução DISPLAY exige ao menos um operando", start_tok)

        return DisplayStmt(
            line=start_tok.line,
            col=start_tok.col,
            operands=operands,
            no_advancing=no_advancing,
        )

    def parse_accept_stmt(self) -> AcceptStmt:
        start_tok = self.advance()  # ACCEPT
        target = self.parse_identifier_ref("variável de destino após ACCEPT")
        return AcceptStmt(line=start_tok.line, col=start_tok.col, target=target)

    def parse_move_stmt(self) -> MoveStmt:
        start_tok = self.advance()  # MOVE
        source = self.parse_operand("operando de origem após MOVE")
        self.consume_word("TO", "Esperado 'TO' após operando de origem em MOVE")

        targets: list[IdentifierRef] = []
        while not self.at_end() and not self.is_statement_boundary():
            target = self.parse_identifier_ref("variável de destino após TO em MOVE")
            if target is None:
                break
            targets.append(target)
            self.skip_optional_separators()

        if not targets:
            self.error("Instrução MOVE exige ao menos uma variável de destino após TO", start_tok)

        return MoveStmt(line=start_tok.line, col=start_tok.col, source=source, targets=targets)

    def parse_perform_stmt(self) -> PerformStmt:
        start_tok = self.advance()  # PERFORM
        target_tok = self.current()
        if target_tok is not None and (self.is_identifier_token(target_tok) or target_tok.type == "NUMBER"):
            target = self.advance().value
        else:
            self.error("Esperado nome de parágrafo ou seção após PERFORM", target_tok)
            target = "<DESCONHECIDO>"

        thru: str | None = None
        if self.match_word("THRU", "THROUGH"):
            self.advance()
            thru_tok = self.current()
            if thru_tok is not None and (self.is_identifier_token(thru_tok) or thru_tok.type == "NUMBER"):
                thru = self.advance().value
            else:
                self.error("Esperado nome de parágrafo após THRU em PERFORM", thru_tok)

        until: ConditionNode | None = None
        times: ExprNode | None = None

        if self.match_word("UNTIL"):
            self.advance()  # UNTIL
            until = self.parse_condition()
        elif not self.at_end() and (self.current().type == "NUMBER" or self.is_identifier_token(self.current())):
            if self.peek(1) and self.peek(1).value == "TIMES":
                times = self.parse_operand()
                self.advance()  # TIMES

        return PerformStmt(
            line=start_tok.line,
            col=start_tok.col,
            target=target,
            thru=thru,
            until=until,
            times=times,
        )

    def parse_stop_stmt(self) -> StopRunStmt:
        start_tok = self.advance()  # STOP
        self.consume_word("RUN", "Esperado 'RUN' após 'STOP'")
        return StopRunStmt(line=start_tok.line, col=start_tok.col)

    def parse_goto_stmt(self) -> GoToStmt:
        start_tok = self.advance()  # GO
        if self.match_word("TO"):
            self.advance()  # TO
        target_tok = self.current()
        if target_tok is not None and (self.is_identifier_token(target_tok) or target_tok.type == "NUMBER"):
            target = self.advance().value
        else:
            self.error("Esperado nome de parágrafo de destino após GO TO", target_tok)
            target = "<DESCONHECIDO>"
        return GoToStmt(line=start_tok.line, col=start_tok.col, target=target)

    def parse_if_stmt(self) -> IfStmt:
        start_tok = self.advance()  # IF
        condition = self.parse_condition()
        if self.match_word("THEN"):
            self.advance()

        then_body = self.parse_statement_list_until(
            "ELSE", "END-IF", "WHEN", "END-EVALUATE", "END-SEARCH"
        )
        if not then_body:
            self.error("Bloco verdadeiro do comando IF não contém nenhuma instrução", start_tok)

        else_body: list[StatementNode] = []
        if self.match_word("ELSE"):
            else_tok = self.advance()  # ELSE
            else_body = self.parse_statement_list_until(
                "ELSE", "END-IF", "WHEN", "END-EVALUATE", "END-SEARCH"
            )
            if not else_body:
                self.error("Bloco ELSE não contém nenhuma instrução", else_tok)

        has_end_if = False
        if self.match_word("END-IF"):
            self.advance()
            has_end_if = True

        return IfStmt(
            line=start_tok.line,
            col=start_tok.col,
            condition=condition,
            then_body=then_body,
            else_body=else_body,
            has_end_if=has_end_if,
        )

    def parse_open_stmt(self) -> OpenStmt:
        start_tok = self.advance()  # OPEN
        groups: list[OpenGroup] = []

        while not self.at_end() and self.match_word("INPUT", "OUTPUT", "I-O", "EXTEND"):
            mode = self.advance().value
            files: list[str] = []
            while (
                not self.at_end()
                and not self.is_statement_boundary()
                and not self.match_word("INPUT", "OUTPUT", "I-O", "EXTEND")
            ):
                f_tok = self.consume_identifier(f"nome de arquivo após OPEN {mode}")
                if f_tok is None:
                    break
                files.append(f_tok.value)
                self.skip_optional_separators()
            if not files:
                self.error(f"Esperado ao menos um nome de arquivo após OPEN {mode}", start_tok)
            groups.append(OpenGroup(mode=mode, files=files))

        if not groups:
            self.error(
                "Instrução OPEN exige o modo de abertura (INPUT, OUTPUT, I-O ou EXTEND) e o arquivo",
                start_tok,
            )

        return OpenStmt(line=start_tok.line, col=start_tok.col, groups=groups)

    def parse_close_stmt(self) -> CloseStmt:
        start_tok = self.advance()  # CLOSE
        files: list[str] = []
        while not self.at_end() and not self.is_statement_boundary():
            f_tok = self.consume_identifier("nome de arquivo após CLOSE")
            if f_tok is None:
                break
            files.append(f_tok.value)
            self.skip_optional_separators()

        if not files:
            self.error("Instrução CLOSE exige ao menos um nome de arquivo", start_tok)

        return CloseStmt(line=start_tok.line, col=start_tok.col, files=files)

    def parse_read_stmt(self) -> ReadStmt:
        start_tok = self.advance()  # READ
        f_tok = self.consume_identifier("nome do arquivo após READ")
        file_name = f_tok.value if f_tok else "<DESCONHECIDO>"

        is_record = False
        if self.match_word("NEXT"):
            self.advance()
        if self.match_word("RECORD"):
            self.advance()
            is_record = True

        into_ref: IdentifierRef | None = None
        if self.match_word("INTO"):
            self.advance()
            into_ref = self.parse_identifier_ref("variável após INTO em READ")

        key_ref: IdentifierRef | None = None
        if self.match_word("KEY"):
            self.advance()
            if self.match_word("IS"):
                self.advance()
            key_ref = self.parse_identifier_ref("chave após KEY em READ")

        invalid_key, not_invalid_key, at_end, not_at_end = self._parse_io_exception_clauses("END-READ")
        return ReadStmt(
            line=start_tok.line,
            col=start_tok.col,
            file_name=file_name,
            is_record=is_record,
            into=into_ref,
            key=key_ref,
            invalid_key=invalid_key,
            not_invalid_key=not_invalid_key,
            at_end=at_end,
            not_at_end=not_at_end,
        )

    def parse_write_stmt(self) -> WriteStmt:
        start_tok = self.advance()  # WRITE
        r_tok = self.consume_identifier("nome do registro após WRITE")
        record_name = r_tok.value if r_tok else "<DESCONHECIDO>"

        from_ref: IdentifierRef | None = None
        if self.match_word("FROM"):
            self.advance()
            from_ref = self.parse_identifier_ref("variável após FROM em WRITE")

        invalid_key, not_invalid_key, _, _ = self._parse_io_exception_clauses("END-WRITE")
        return WriteStmt(
            line=start_tok.line,
            col=start_tok.col,
            record_name=record_name,
            from_id=from_ref,
            invalid_key=invalid_key,
            not_invalid_key=not_invalid_key,
        )

    def parse_start_stmt(self) -> StartStmt:
        start_tok = self.advance()  # START
        f_tok = self.consume_identifier("nome do arquivo após START")
        file_name = f_tok.value if f_tok else "<DESCONHECIDO>"

        key_op: str | None = None
        key_name: IdentifierRef | None = None

        if self.match_word("KEY"):
            self.advance()  # KEY
            if self.match_word("IS"):
                self.advance()
            key_op = self.parse_relational_operator()
            key_name = self.parse_identifier_ref("nome da chave após operador em START KEY")

        invalid_key, not_invalid_key, _, _ = self._parse_io_exception_clauses("END-START")
        return StartStmt(
            line=start_tok.line,
            col=start_tok.col,
            file_name=file_name,
            key_operator=key_op,
            key_name=key_name,
            invalid_key=invalid_key,
            not_invalid_key=not_invalid_key,
        )

    def _parse_io_exception_clauses(
        self, end_delimiter: str
    ) -> tuple[list[StatementNode], list[StatementNode], list[StatementNode], list[StatementNode]]:
        invalid_key: list[StatementNode] = []
        not_invalid_key: list[StatementNode] = []
        at_end: list[StatementNode] = []
        not_at_end: list[StatementNode] = []

        while not self.at_end():
            if self.match_word("INVALID"):
                self.advance()  # INVALID
                if self.match_word("KEY"):
                    self.advance()
                invalid_key = self.parse_statement_list_until(
                    "NOT", "AT", end_delimiter, "ELSE", "END-IF", "WHEN", "END-EVALUATE"
                )
            elif (
                self.match_word("NOT")
                and self.peek(1)
                and self.peek(1).value == "INVALID"
            ):
                self.advance()  # NOT
                self.advance()  # INVALID
                if self.match_word("KEY"):
                    self.advance()
                not_invalid_key = self.parse_statement_list_until(
                    "INVALID", "AT", end_delimiter, "ELSE", "END-IF", "WHEN", "END-EVALUATE"
                )
            elif self.match_word("AT"):
                self.advance()  # AT
                self.consume_word("END", "Esperado 'END' após 'AT'")
                at_end = self.parse_statement_list_until(
                    "NOT", "INVALID", end_delimiter, "ELSE", "END-IF", "WHEN", "END-EVALUATE"
                )
            elif self.match_word("NOT") and self.peek(1) and self.peek(1).value == "AT":
                self.advance()  # NOT
                self.advance()  # AT
                self.consume_word("END", "Esperado 'END' após 'NOT AT'")
                not_at_end = self.parse_statement_list_until(
                    "INVALID", "AT", end_delimiter, "ELSE", "END-IF", "WHEN", "END-EVALUATE"
                )
            else:
                break

        if self.match_word(end_delimiter):
            self.advance()

        return invalid_key, not_invalid_key, at_end, not_at_end

    def _parse_size_error_clauses(
        self, end_delimiter: str
    ) -> tuple[list[StatementNode], list[StatementNode]]:
        on_size_error: list[StatementNode] = []
        not_on_size_error: list[StatementNode] = []

        while not self.at_end():
            if self.match_word("ON") and self.peek(1) and self.peek(1).value == "SIZE":
                self.advance()  # ON
                self.advance()  # SIZE
                self.consume_word("ERROR", "Esperado 'ERROR' após 'ON SIZE'")
                on_size_error = self.parse_statement_list_until(
                    "NOT", end_delimiter, "ELSE", "END-IF", "WHEN", "END-EVALUATE"
                )
            elif self.match_word("SIZE") and self.peek(1) and self.peek(1).value == "ERROR":
                self.advance()  # SIZE
                self.advance()  # ERROR
                on_size_error = self.parse_statement_list_until(
                    "NOT", end_delimiter, "ELSE", "END-IF", "WHEN", "END-EVALUATE"
                )
            elif self.match_word("NOT") and self.peek(1) and self.peek(1).value in ("ON", "SIZE"):
                self.advance()  # NOT
                if self.match_word("ON"):
                    self.advance()
                self.consume_word("SIZE", "Esperado 'SIZE' após 'NOT [ON]'")
                self.consume_word("ERROR", "Esperado 'ERROR' após 'SIZE'")
                not_on_size_error = self.parse_statement_list_until(
                    end_delimiter, "ELSE", "END-IF", "WHEN", "END-EVALUATE"
                )
            else:
                break

        if self.match_word(end_delimiter):
            self.advance()

        return on_size_error, not_on_size_error

    def parse_add_stmt(self) -> AddStmt:
        start_tok = self.advance()  # ADD
        operands: list[ExprNode] = []
        while (
            not self.at_end()
            and not self.is_statement_boundary()
            and not self.match_word("TO", "GIVING")
        ):
            op = self.parse_operand("operando em ADD")
            if op is None:
                break
            operands.append(op)
            self.skip_optional_separators()

        to_targets: list[IdentifierRef] = []
        if self.match_word("TO"):
            self.advance()  # TO
            while (
                not self.at_end()
                and not self.is_statement_boundary()
                and not self.match_word("GIVING")
            ):
                t = self.parse_identifier_ref("destino após TO em ADD")
                if t is None:
                    break
                to_targets.append(t)
                self.skip_optional_separators()

        giving_targets: list[IdentifierRef] = []
        if self.match_word("GIVING"):
            self.advance()  # GIVING
            while not self.at_end() and not self.is_statement_boundary():
                t = self.parse_identifier_ref("destino após GIVING em ADD")
                if t is None:
                    break
                giving_targets.append(t)
                self.skip_optional_separators()

        if not operands or (not to_targets and not giving_targets):
            self.error("Instrução ADD exige operandos e cláusula TO e/ou GIVING", start_tok)

        on_err, not_on_err = self._parse_size_error_clauses("END-ADD")
        return AddStmt(
            line=start_tok.line,
            col=start_tok.col,
            operands=operands,
            to_targets=to_targets,
            giving_targets=giving_targets,
            on_size_error=on_err,
            not_on_size_error=not_on_err,
        )

    def parse_subtract_stmt(self) -> SubtractStmt:
        start_tok = self.advance()  # SUBTRACT
        operands: list[ExprNode] = []
        while (
            not self.at_end()
            and not self.is_statement_boundary()
            and not self.match_word("FROM")
        ):
            op = self.parse_operand("operando em SUBTRACT")
            if op is None:
                break
            operands.append(op)
            self.skip_optional_separators()

        from_targets: list[IdentifierRef] = []
        if self.consume_word("FROM", "Esperado 'FROM' na instrução SUBTRACT"):
            while (
                not self.at_end()
                and not self.is_statement_boundary()
                and not self.match_word("GIVING")
            ):
                t = self.parse_identifier_ref("variável após FROM em SUBTRACT")
                if t is None:
                    break
                from_targets.append(t)
                self.skip_optional_separators()

        giving_targets: list[IdentifierRef] = []
        if self.match_word("GIVING"):
            self.advance()  # GIVING
            while not self.at_end() and not self.is_statement_boundary():
                t = self.parse_identifier_ref("variável após GIVING em SUBTRACT")
                if t is None:
                    break
                giving_targets.append(t)
                self.skip_optional_separators()

        on_err, not_on_err = self._parse_size_error_clauses("END-SUBTRACT")
        return SubtractStmt(
            line=start_tok.line,
            col=start_tok.col,
            operands=operands,
            from_targets=from_targets,
            giving_targets=giving_targets,
            on_size_error=on_err,
            not_on_size_error=not_on_err,
        )

    def parse_multiply_stmt(self) -> MultiplyStmt:
        start_tok = self.advance()  # MULTIPLY
        operand = self.parse_operand("operando após MULTIPLY")
        self.consume_word("BY", "Esperado 'BY' na instrução MULTIPLY")

        by_targets: list[IdentifierRef] = []
        while (
            not self.at_end()
            and not self.is_statement_boundary()
            and not self.match_word("GIVING")
        ):
            t = self.parse_identifier_ref("operando após BY em MULTIPLY")
            if t is None:
                break
            by_targets.append(t)
            self.skip_optional_separators()

        giving_targets: list[IdentifierRef] = []
        if self.match_word("GIVING"):
            self.advance()  # GIVING
            while not self.at_end() and not self.is_statement_boundary():
                t = self.parse_identifier_ref("variável após GIVING em MULTIPLY")
                if t is None:
                    break
                giving_targets.append(t)
                self.skip_optional_separators()

        on_err, not_on_err = self._parse_size_error_clauses("END-MULTIPLY")
        return MultiplyStmt(
            line=start_tok.line,
            col=start_tok.col,
            operand=operand,
            by_targets=by_targets,
            giving_targets=giving_targets,
            on_size_error=on_err,
            not_on_size_error=not_on_err,
        )

    def parse_divide_stmt(self) -> DivideStmt:
        start_tok = self.advance()  # DIVIDE
        operand = self.parse_operand("operando após DIVIDE")

        mode = "BY"
        if self.match_word("BY", "INTO"):
            mode = self.advance().value
        else:
            self.error("Esperado 'BY' ou 'INTO' na instrução DIVIDE", self.current())

        targets: list[IdentifierRef | ExprNode] = []
        while (
            not self.at_end()
            and not self.is_statement_boundary()
            and not self.match_word("GIVING", "REMAINDER")
        ):
            op = self.parse_operand("operando após BY/INTO em DIVIDE")
            if op is None:
                break
            targets.append(op)
            self.skip_optional_separators()

        giving_targets: list[IdentifierRef] = []
        if self.match_word("GIVING"):
            self.advance()  # GIVING
            while (
                not self.at_end()
                and not self.is_statement_boundary()
                and not self.match_word("REMAINDER")
            ):
                t = self.parse_identifier_ref("variável após GIVING em DIVIDE")
                if t is None:
                    break
                giving_targets.append(t)
                self.skip_optional_separators()

        remainder: IdentifierRef | None = None
        if self.match_word("REMAINDER"):
            self.advance()  # REMAINDER
            remainder = self.parse_identifier_ref("variável após REMAINDER em DIVIDE")

        on_err, not_on_err = self._parse_size_error_clauses("END-DIVIDE")
        return DivideStmt(
            line=start_tok.line,
            col=start_tok.col,
            operand=operand,
            mode=mode,
            targets=targets,
            giving_targets=giving_targets,
            remainder=remainder,
            on_size_error=on_err,
            not_on_size_error=not_on_err,
        )

    def parse_compute_stmt(self) -> ComputeStmt:
        start_tok = self.advance()  # COMPUTE
        targets: list[IdentifierRef] = []
        while (
            not self.at_end()
            and not self.is_statement_boundary()
            and self.current().type != "EQUAL"
            and not self.match_word("EQUAL")
        ):
            t = self.parse_identifier_ref("variável de destino em COMPUTE")
            if t is None:
                break
            targets.append(t)
            if self.match_word("ROUNDED"):
                self.advance()
            self.skip_optional_separators()

        if not self.at_end() and (self.current().type == "EQUAL" or self.match_word("EQUAL")):
            self.advance()
        else:
            self.error("Esperado '=' na instrução COMPUTE", self.current())

        expr = self.parse_arithmetic_expression()
        on_err, not_on_err = self._parse_size_error_clauses("END-COMPUTE")
        return ComputeStmt(
            line=start_tok.line,
            col=start_tok.col,
            targets=targets,
            expression=expr,
            on_size_error=on_err,
            not_on_size_error=not_on_err,
        )

    def parse_evaluate_stmt(self) -> EvaluateStmt:
        start_tok = self.advance()  # EVALUATE
        if self.match_word("TRUE", "FALSE"):
            tok = self.advance()
            subject: ExprNode | None = LiteralNode(
                line=tok.line, col=tok.col, literal_type="BOOLEAN", value=tok.value
            )
        else:
            subject = self.parse_arithmetic_expression()

        branches: list[EvaluateWhenBranch] = []
        while not self.at_end() and self.match_word("WHEN"):
            when_tok = self.advance()  # WHEN
            if self.match_word("OTHER"):
                self.advance()
                cond: ExprNode | str = "OTHER"
            else:
                cond_expr = self.parse_arithmetic_expression()
                cond = cond_expr if cond_expr is not None else "INVALID"

            body = self.parse_statement_list_until("WHEN", "END-EVALUATE")
            branches.append(
                EvaluateWhenBranch(line=when_tok.line, col=when_tok.col, condition=cond, body=body)
            )

        if not branches:
            self.error("Instrução EVALUATE exige ao menos uma cláusula WHEN", start_tok)

        if self.match_word("END-EVALUATE"):
            self.advance()

        return EvaluateStmt(
            line=start_tok.line, col=start_tok.col, subject=subject, branches=branches
        )

    def parse_search_stmt(self) -> SearchStmt:
        start_tok = self.advance()  # SEARCH
        if self.match_word("ALL"):
            self.advance()
        table = self.parse_identifier_ref("nome da tabela após SEARCH")

        at_end: list[StatementNode] = []
        if self.match_word("AT"):
            self.advance()  # AT
            self.consume_word("END", "Esperado 'END' após 'AT' em SEARCH")
            at_end = self.parse_statement_list_until("WHEN", "END-SEARCH")

        branches: list[SearchWhenBranch] = []
        while not self.at_end() and self.match_word("WHEN"):
            when_tok = self.advance()  # WHEN
            cond = self.parse_condition()
            body = self.parse_statement_list_until("WHEN", "END-SEARCH")
            branches.append(
                SearchWhenBranch(line=when_tok.line, col=when_tok.col, condition=cond, body=body)
            )

        if not branches:
            self.error("Instrução SEARCH exige ao menos uma cláusula WHEN", start_tok)

        if self.match_word("END-SEARCH"):
            self.advance()

        return SearchStmt(
            line=start_tok.line, col=start_tok.col, table=table, at_end=at_end, branches=branches
        )

    def parse_set_stmt(self) -> SetStmt:
        start_tok = self.advance()  # SET
        targets: list[IdentifierRef] = []
        while (
            not self.at_end()
            and not self.is_statement_boundary()
            and not self.match_word("TO", "UP", "DOWN")
        ):
            t = self.parse_identifier_ref("variável ou índice após SET")
            if t is None:
                break
            targets.append(t)
            self.skip_optional_separators()

        mode = "TO"
        if self.match_word("TO"):
            self.advance()
            mode = "TO"
        elif self.match_word("UP", "DOWN"):
            dir_word = self.advance().value
            self.consume_word("BY", f"Esperado 'BY' após '{dir_word}' em SET")
            mode = f"{dir_word} BY"
        else:
            self.error("Esperado 'TO', 'UP BY' ou 'DOWN BY' na instrução SET", self.current())

        val = self.parse_operand("valor após TO/BY em SET")
        return SetStmt(line=start_tok.line, col=start_tok.col, targets=targets, mode=mode, value=val)

    def parse_inspect_stmt(self) -> InspectStmt:
        start_tok = self.advance()  # INSPECT
        target = self.parse_identifier_ref("variável alvo após INSPECT")

        mode = ""
        if self.match_word("TALLYING", "REPLACING", "CONVERTING"):
            mode = self.advance().value
        else:
            self.error(
                "Esperado 'TALLYING', 'REPLACING' ou 'CONVERTING' na instrução INSPECT",
                self.current(),
            )

        clauses: list[str] = []
        while not self.at_end() and not self.is_statement_boundary():
            tok = self.advance()
            if tok.type == "ALPHANUMERIC_LITERAL":
                clauses.append(f'"{tok.value}"')
            else:
                clauses.append(tok.value)

        return InspectStmt(
            line=start_tok.line, col=start_tok.col, target=target, mode=mode, clauses=clauses
        )

    # ==========================================================================
    # EXPRESSÕES ARITMÉTICAS, CONDIÇÕES LÓGICAS E REFERÊNCIAS DE DADOS
    # ==========================================================================

    def parse_operand(self, context: str = "operando") -> ExprNode | None:
        tok = self.current()
        if tok is None:
            self.error(f"Esperado {context}, encontrado fim de arquivo")
            return None

        # Literal numérico ou alfanumérico
        if tok.type in LITERAL_TOKEN_TYPES:
            self.advance()
            return LiteralNode(line=tok.line, col=tok.col, literal_type=tok.type, value=tok.value)

        # Constante figurativa (ZERO, ZEROS, SPACES, LOW-VALUES, etc.)
        if tok.value in FIGURATIVE_CONSTANTS:
            self.advance()
            return LiteralNode(
                line=tok.line,
                col=tok.col,
                literal_type="FIGURATIVE_CONSTANT",
                value=tok.value,
            )

        # Chamada de função intrínseca: FUNCTION <Nome>([arg, ...])
        if self.match_word("FUNCTION"):
            f_tok = self.advance()  # FUNCTION
            fn_tok = self.consume_identifier("nome da função após FUNCTION")
            fn_name = fn_tok.value if fn_tok else "<FUNCAO>"
            args: list[ExprNode] = []
            if not self.at_end() and self.current().type == "LEFT_PAREN":
                self.advance()  # (
                while not self.at_end() and self.current().type != "RIGHT_PAREN":
                    if self.current().type == "DOT":
                        break
                    arg = self.parse_arithmetic_expression()
                    if arg is None:
                        break
                    args.append(arg)
                    self.skip_optional_separators()
                if not self.at_end() and self.current().type == "RIGHT_PAREN":
                    self.advance()  # )
                else:
                    self.error(f"Esperado ')' após argumentos de FUNCTION {fn_name}", self.current())
            return FunctionCallNode(
                line=f_tok.line, col=f_tok.col, function_name=fn_name, arguments=args
            )

        # Identificador (simples, qualificado por OF/IN ou subscrito/indexado)
        if self.is_identifier_token(tok):
            return self.parse_identifier_ref(context)

        self.error(f"Esperado {context}, encontrado '{tok.value}'", tok)
        return None

    def parse_identifier_ref(self, context: str = "identificador") -> IdentifierRef | None:
        tok = self.consume_identifier(context)
        if tok is None:
            return None

        qualifiers: list[str] = []
        while not self.at_end() and self.match_word("OF", "IN"):
            self.advance()  # OF / IN
            q_tok = self.consume_identifier("nome do grupo qualificador após OF/IN")
            if q_tok is None:
                break
            qualifiers.append(q_tok.value)

        subscripts: list[ExprNode] = []
        if not self.at_end() and self.current().type == "LEFT_PAREN":
            self.advance()  # (
            while not self.at_end() and self.current().type != "RIGHT_PAREN":
                if self.current().type == "DOT":
                    break
                sub = self.parse_arithmetic_expression()
                if sub is None:
                    break
                subscripts.append(sub)
                self.skip_optional_separators()
            if not self.at_end() and self.current().type == "RIGHT_PAREN":
                self.advance()  # )
            else:
                self.error(f"Esperado ')' ao fechar índice/subscrito de '{tok.value}'", self.current())

        return IdentifierRef(
            line=tok.line,
            col=tok.col,
            name=tok.value,
            qualifiers=qualifiers,
            subscripts=subscripts,
        )

    def parse_arithmetic_expression(self) -> ExprNode | None:
        return self._parse_additive()

    def _parse_additive(self) -> ExprNode | None:
        left = self._parse_multiplicative()
        if left is None:
            return None
        while not self.at_end() and self.current().type in ("PLUS", "MINUS"):
            op_tok = self.advance()
            right = self._parse_multiplicative()
            if right is None:
                break
            left = BinaryExprNode(
                line=op_tok.line, col=op_tok.col, operator=op_tok.value, left=left, right=right
            )
        return left

    def _parse_multiplicative(self) -> ExprNode | None:
        left = self._parse_power()
        if left is None:
            return None
        while not self.at_end() and self.current().type in ("MULTIPLY", "DIVIDE"):
            op_tok = self.advance()
            right = self._parse_power()
            if right is None:
                break
            left = BinaryExprNode(
                line=op_tok.line, col=op_tok.col, operator=op_tok.value, left=left, right=right
            )
        return left

    def _parse_power(self) -> ExprNode | None:
        left = self._parse_unary()
        if left is None:
            return None
        if not self.at_end() and self.current().type == "POWER":
            op_tok = self.advance()
            right = self._parse_power()
            if right is not None:
                left = BinaryExprNode(
                    line=op_tok.line, col=op_tok.col, operator=op_tok.value, left=left, right=right
                )
        return left

    def _parse_unary(self) -> ExprNode | None:
        if not self.at_end() and self.current().type in ("PLUS", "MINUS"):
            op_tok = self.advance()
            operand = self._parse_unary()
            if operand is not None:
                return UnaryExprNode(
                    line=op_tok.line, col=op_tok.col, operator=op_tok.value, operand=operand
                )
        if not self.at_end() and self.current().type == "LEFT_PAREN":
            self.advance()  # (
            expr = self.parse_arithmetic_expression()
            if not self.at_end() and self.current().type == "RIGHT_PAREN":
                self.advance()  # )
            else:
                self.error("Esperado ')' ao fechar expressão entre parênteses", self.current())
            return expr
        return self.parse_operand("expressão")

    def parse_condition(self) -> ConditionNode | None:
        return self._parse_or_condition()

    def _parse_or_condition(self) -> ConditionNode | None:
        left = self._parse_and_condition()
        if left is None:
            return None

        # Mantém o sujeito e operador da última condição relacional para condições abreviadas
        # Ex: IF WKS-OPCAO = "S" OR "U" OR "M"
        last_rel = self._find_last_relation(left)

        while not self.at_end() and self.match_word("OR"):
            or_tok = self.advance()  # OR
            right = self._parse_and_condition(inherited_rel=last_rel)
            if right is None:
                break
            new_rel = self._find_last_relation(right)
            if new_rel is not None:
                last_rel = new_rel
            left = LogicalConditionNode(
                line=or_tok.line, col=or_tok.col, operator="OR", left=left, right=right
            )
        return left

    def _parse_and_condition(
        self, inherited_rel: RelationConditionNode | None = None
    ) -> ConditionNode | None:
        left = self._parse_not_condition(inherited_rel=inherited_rel)
        if left is None:
            return None
        last_rel = self._find_last_relation(left) or inherited_rel

        while not self.at_end() and self.match_word("AND"):
            and_tok = self.advance()  # AND
            right = self._parse_not_condition(inherited_rel=last_rel)
            if right is None:
                break
            new_rel = self._find_last_relation(right)
            if new_rel is not None:
                last_rel = new_rel
            left = LogicalConditionNode(
                line=and_tok.line, col=and_tok.col, operator="AND", left=left, right=right
            )
        return left

    def _parse_not_condition(
        self, inherited_rel: RelationConditionNode | None = None
    ) -> ConditionNode | None:
        if not self.at_end() and self.match_word("NOT"):
            not_tok = self.advance()
            op = self._parse_primary_condition(inherited_rel=inherited_rel)
            if op is not None:
                return NotConditionNode(line=not_tok.line, col=not_tok.col, operand=op)
            return None
        return self._parse_primary_condition(inherited_rel=inherited_rel)

    def _parse_primary_condition(
        self, inherited_rel: RelationConditionNode | None = None
    ) -> ConditionNode | None:
        left_expr = self.parse_arithmetic_expression()
        if left_expr is None:
            return None

        if self._is_relational_operator_ahead():
            op_str = self.parse_relational_operator()
            right_expr = self.parse_arithmetic_expression()
            if right_expr is None:
                return None
            return RelationConditionNode(
                line=left_expr.line,
                col=left_expr.col,
                operator=op_str,
                left=left_expr,
                right=right_expr,
            )

        # Recuperação de operador relacional inválido (ex: IF WKS-SAIR == 1 em prog003_calculadora)
        tok = self.current()
        if tok is not None and tok.type == "ERROR_INVALID_ID" and any(c in tok.value for c in "=<>!"):
            bad_op = self.advance()
            self.error(f"Operador relacional inválido em COBOL: '{bad_op.value}'", bad_op)
            right_expr = self.parse_arithmetic_expression()
            if right_expr is not None:
                return RelationConditionNode(
                    line=left_expr.line,
                    col=left_expr.col,
                    operator=bad_op.value,
                    left=left_expr,
                    right=right_expr,
                )

        # Condição relacional abreviada: ex. IF WKS-OPCAO = "S" OR "U"
        if inherited_rel is not None:
            return RelationConditionNode(
                line=left_expr.line,
                col=left_expr.col,
                operator=inherited_rel.operator,
                left=inherited_rel.left,
                right=left_expr,
            )

        # Condição simples (ex: IF VALOR-1 em test.cob ou condition-name de nível 88)
        return SimpleConditionNode(line=left_expr.line, col=left_expr.col, operand=left_expr)

    @staticmethod
    def _find_last_relation(cond: ConditionNode | None) -> RelationConditionNode | None:
        if isinstance(cond, RelationConditionNode):
            return cond
        if isinstance(cond, LogicalConditionNode):
            return Parser._find_last_relation(cond.right) or Parser._find_last_relation(cond.left)
        if isinstance(cond, NotConditionNode):
            return Parser._find_last_relation(cond.operand)
        return None

    def _is_relational_operator_ahead(self) -> bool:
        tok = self.current()
        if tok is None:
            return False
        if tok.type in ("EQUAL", "GREATER_THAN", "LESS_THAN", "GREATER_EQUAL", "LESS_EQUAL"):
            return True
        if self.match_word("IS", "EQUAL", "GREATER", "LESS"):
            return True
        if self.match_word("NOT") and self.peek(1) and self.peek(1).value in (
            "=", ">", "<", ">=", "<=", "EQUAL", "GREATER", "LESS"
        ):
            return True
        return False

    def parse_relational_operator(self) -> str:
        parts: list[str] = []
        if self.match_word("IS"):
            self.advance()
        if self.match_word("NOT"):
            parts.append(self.advance().value)

        tok = self.current()
        if tok is None:
            return " ".join(parts)

        if tok.type in ("EQUAL", "GREATER_THAN", "LESS_THAN", "GREATER_EQUAL", "LESS_EQUAL"):
            parts.append(self.advance().value)
        elif self.match_word("EQUAL"):
            parts.append(self.advance().value)
            if self.match_word("TO"):
                self.advance()
        elif self.match_word("GREATER", "LESS"):
            parts.append(self.advance().value)
            if self.match_word("THAN"):
                self.advance()
            if self.match_word("OR") and self.peek(1) and self.peek(1).value == "EQUAL":
                self.advance()  # OR
                self.advance()  # EQUAL
                if self.match_word("TO"):
                    self.advance()
                parts.append("OR EQUAL")
        else:
            self.error(f"Esperado operador relacional, encontrado '{tok.value}'", tok)

        return " ".join(parts)


# ==============================================================================
# FORMATAÇÃO E IMPRESSÃO HIERÁRQUICA DA AST
# ==============================================================================


def format_expr(expr: ExprNode | None) -> str:
    if expr is None:
        return "<erro>"
    if isinstance(expr, LiteralNode):
        if expr.literal_type == "ALPHANUMERIC_LITERAL":
            return f'"{expr.value}"'
        return expr.value
    if isinstance(expr, IdentifierRef):
        base = expr.name
        if expr.qualifiers:
            base += " OF " + " OF ".join(expr.qualifiers)
        if expr.subscripts:
            subs = ", ".join(format_expr(s) for s in expr.subscripts)
            base += f"({subs})"
        return base
    if isinstance(expr, FunctionCallNode):
        args = ", ".join(format_expr(a) for a in expr.arguments)
        return f"FUNCTION {expr.function_name}({args})"
    if isinstance(expr, UnaryExprNode):
        return f"({expr.operator}{format_expr(expr.operand)})"
    if isinstance(expr, BinaryExprNode):
        return f"({format_expr(expr.left)} {expr.operator} {format_expr(expr.right)})"
    return str(expr)


def format_condition(cond: ConditionNode | None) -> str:
    if cond is None:
        return "<condição inválida>"
    if isinstance(cond, SimpleConditionNode):
        return format_expr(cond.operand)
    if isinstance(cond, RelationConditionNode):
        return f"{format_expr(cond.left)} {cond.operator} {format_expr(cond.right)}"
    if isinstance(cond, NotConditionNode):
        return f"NOT ({format_condition(cond.operand)})"
    if isinstance(cond, LogicalConditionNode):
        return f"({format_condition(cond.left)} {cond.operator} {format_condition(cond.right)})"
    return str(cond)


def format_ast(program: ProgramNode) -> str:
    lines: list[str] = []

    def emit(indent: int, text: str) -> None:
        lines.append("  " * indent + text)

    def emit_data_item(indent: int, item: DataItemNode) -> None:
        details = [f"level={item.level:02d}", f"name={item.name}"]
        if item.pic is not None:
            details.append(f"pic={item.pic}")
        if item.value is not None:
            details.append(f"value={item.value}")
        if item.occurs is not None:
            occ = f"occurs={item.occurs.times}"
            if item.occurs.indexed_by:
                occ += f" INDEXED BY {','.join(item.occurs.indexed_by)}"
            details.append(occ)
        emit(indent, f"DataItem({', '.join(details)}) [linha {item.line}]")
        for child in item.children:
            emit_data_item(indent + 1, child)

    def emit_statement(indent: int, stmt: StatementNode) -> None:
        loc = f"[linha {stmt.line}]"
        if isinstance(stmt, DisplayStmt):
            ops = ", ".join(format_expr(o) for o in stmt.operands)
            adv = " WITH NO ADVANCING" if stmt.no_advancing else ""
            emit(indent, f"DisplayStmt({ops}{adv}) {loc}")
        elif isinstance(stmt, AcceptStmt):
            emit(indent, f"AcceptStmt(target={format_expr(stmt.target)}) {loc}")
        elif isinstance(stmt, MoveStmt):
            targets = ", ".join(format_expr(t) for t in stmt.targets)
            emit(indent, f"MoveStmt(from={format_expr(stmt.source)} -> to=[{targets}]) {loc}")
        elif isinstance(stmt, PerformStmt):
            parts = [f"target={stmt.target}"]
            if stmt.thru:
                parts.append(f"thru={stmt.thru}")
            if stmt.until:
                parts.append(f"until=({format_condition(stmt.until)})")
            if stmt.times:
                parts.append(f"times={format_expr(stmt.times)}")
            emit(indent, f"PerformStmt({', '.join(parts)}) {loc}")
        elif isinstance(stmt, StopRunStmt):
            emit(indent, f"StopRunStmt() {loc}")
        elif isinstance(stmt, GoToStmt):
            emit(indent, f"GoToStmt(target={stmt.target}) {loc}")
        elif isinstance(stmt, IfStmt):
            emit(indent, f"IfStmt(condition={format_condition(stmt.condition)}) {loc}")
            emit(indent + 1, "THEN:")
            for s in stmt.then_body:
                emit_statement(indent + 2, s)
            if stmt.else_body:
                emit(indent + 1, "ELSE:")
                for s in stmt.else_body:
                    emit_statement(indent + 2, s)
        elif isinstance(stmt, OpenStmt):
            grps = "; ".join(f"{g.mode}: {', '.join(g.files)}" for g in stmt.groups)
            emit(indent, f"OpenStmt({grps}) {loc}")
        elif isinstance(stmt, CloseStmt):
            emit(indent, f"CloseStmt(files=[{', '.join(stmt.files)}]) {loc}")
        elif isinstance(stmt, ReadStmt):
            parts = [f"file={stmt.file_name}"]
            if stmt.into:
                parts.append(f"into={format_expr(stmt.into)}")
            if stmt.key:
                parts.append(f"key={format_expr(stmt.key)}")
            emit(indent, f"ReadStmt({', '.join(parts)}) {loc}")
            if stmt.invalid_key:
                emit(indent + 1, "INVALID KEY:")
                for s in stmt.invalid_key:
                    emit_statement(indent + 2, s)
            if stmt.not_invalid_key:
                emit(indent + 1, "NOT INVALID KEY:")
                for s in stmt.not_invalid_key:
                    emit_statement(indent + 2, s)
            if stmt.at_end:
                emit(indent + 1, "AT END:")
                for s in stmt.at_end:
                    emit_statement(indent + 2, s)
        elif isinstance(stmt, WriteStmt):
            parts = [f"record={stmt.record_name}"]
            if stmt.from_id:
                parts.append(f"from={format_expr(stmt.from_id)}")
            emit(indent, f"WriteStmt({', '.join(parts)}) {loc}")
            if stmt.invalid_key:
                emit(indent + 1, "INVALID KEY:")
                for s in stmt.invalid_key:
                    emit_statement(indent + 2, s)
            if stmt.not_invalid_key:
                emit(indent + 1, "NOT INVALID KEY:")
                for s in stmt.not_invalid_key:
                    emit_statement(indent + 2, s)
        elif isinstance(stmt, StartStmt):
            parts = [f"file={stmt.file_name}"]
            if stmt.key_name:
                parts.append(f"key={stmt.key_operator} {format_expr(stmt.key_name)}")
            emit(indent, f"StartStmt({', '.join(parts)}) {loc}")
            if stmt.invalid_key:
                emit(indent + 1, "INVALID KEY:")
                for s in stmt.invalid_key:
                    emit_statement(indent + 2, s)
        elif isinstance(stmt, AddStmt):
            ops = ", ".join(format_expr(o) for o in stmt.operands)
            tos = ", ".join(format_expr(t) for t in stmt.to_targets)
            giv = ", ".join(format_expr(g) for g in stmt.giving_targets)
            emit(indent, f"AddStmt(operands=[{ops}], to=[{tos}], giving=[{giv}]) {loc}")
            if stmt.on_size_error:
                emit(indent + 1, "ON SIZE ERROR:")
                for s in stmt.on_size_error:
                    emit_statement(indent + 2, s)
        elif isinstance(stmt, SubtractStmt):
            ops = ", ".join(format_expr(o) for o in stmt.operands)
            frm = ", ".join(format_expr(t) for t in stmt.from_targets)
            giv = ", ".join(format_expr(g) for g in stmt.giving_targets)
            emit(indent, f"SubtractStmt(operands=[{ops}], from=[{frm}], giving=[{giv}]) {loc}")
        elif isinstance(stmt, MultiplyStmt):
            bys = ", ".join(format_expr(t) for t in stmt.by_targets)
            giv = ", ".join(format_expr(g) for g in stmt.giving_targets)
            emit(indent, f"MultiplyStmt(operand={format_expr(stmt.operand)}, by=[{bys}], giving=[{giv}]) {loc}")
        elif isinstance(stmt, DivideStmt):
            tgts = ", ".join(format_expr(t) for t in stmt.targets)
            giv = ", ".join(format_expr(g) for g in stmt.giving_targets)
            rem = f", remainder={format_expr(stmt.remainder)}" if stmt.remainder else ""
            emit(
                indent,
                f"DivideStmt(operand={format_expr(stmt.operand)}, {stmt.mode}=[{tgts}], giving=[{giv}]{rem}) {loc}",
            )
        elif isinstance(stmt, ComputeStmt):
            tgts = ", ".join(format_expr(t) for t in stmt.targets)
            emit(indent, f"ComputeStmt(targets=[{tgts}], expr={format_expr(stmt.expression)}) {loc}")
        elif isinstance(stmt, EvaluateStmt):
            emit(indent, f"EvaluateStmt(subject={format_expr(stmt.subject)}) {loc}")
            for br in stmt.branches:
                cond_str = br.condition if isinstance(br.condition, str) else format_expr(br.condition)
                emit(indent + 1, f"WHEN {cond_str}: [linha {br.line}]")
                for s in br.body:
                    emit_statement(indent + 2, s)
        elif isinstance(stmt, SearchStmt):
            emit(indent, f"SearchStmt(table={format_expr(stmt.table)}) {loc}")
            if stmt.at_end:
                emit(indent + 1, "AT END:")
                for s in stmt.at_end:
                    emit_statement(indent + 2, s)
            for br in stmt.branches:
                emit(indent + 1, f"WHEN {format_condition(br.condition)}: [linha {br.line}]")
                for s in br.body:
                    emit_statement(indent + 2, s)
        elif isinstance(stmt, SetStmt):
            tgts = ", ".join(format_expr(t) for t in stmt.targets)
            emit(indent, f"SetStmt(targets=[{tgts}], {stmt.mode}={format_expr(stmt.value)}) {loc}")
        elif isinstance(stmt, InspectStmt):
            cls_str = " ".join(stmt.clauses)
            emit(indent, f"InspectStmt(target={format_expr(stmt.target)}, mode={stmt.mode}, {cls_str}) {loc}")
        else:
            emit(indent, f"{type(stmt).__name__}() {loc}")

    def emit_sentence(indent: int, sent: SentenceNode) -> None:
        emit(indent, f"Sentence [linha {sent.line}]")
        for stmt in sent.statements:
            emit_statement(indent + 1, stmt)

    def emit_paragraph(indent: int, para: ParagraphNode) -> None:
        emit(indent, f"Paragraph(name={para.name}) [linha {para.line}]")
        for sent in para.sentences:
            emit_sentence(indent + 1, sent)

    end_info = f", end_program={program.end_program}" if program.end_program else ""
    emit(0, f"Program({end_info.lstrip(', ')}) [linha {program.line}]")

    if program.identification_division:
        id_div = program.identification_division
        emit(1, f"IdentificationDivision(program_id={id_div.program_id}) [linha {id_div.line}]")
        for p in id_div.paragraphs:
            text = " | ".join(p.entries)
            emit(2, f"CommentParagraph(name={p.name}, text={text!r}) [linha {p.line}]")

    if program.environment_division:
        env_div = program.environment_division
        emit(1, f"EnvironmentDivision [linha {env_div.line}]")
        if env_div.configuration_section:
            cfg = env_div.configuration_section
            emit(
                2,
                f"ConfigurationSection(source={cfg.source_computer}, object={cfg.object_computer}) [linha {cfg.line}]",
            )
        if env_div.input_output_section:
            io = env_div.input_output_section
            emit(2, f"InputOutputSection [linha {io.line}]")
            for sel in io.file_control:
                cls_str = ", ".join(f"{c.clause_type}={c.value}" for c in sel.clauses)
                extra = f", {cls_str}" if cls_str else ""
                emit(3, f"Select(file={sel.file_name}, assign_to={sel.assign_to!r}{extra}) [linha {sel.line}]")

    if program.data_division:
        data_div = program.data_division
        emit(1, f"DataDivision [linha {data_div.line}]")
        if data_div.file_section:
            emit(2, "FileSection")
            for fd in data_div.file_section:
                cls_str = f", clauses={fd.clauses}" if fd.clauses else ""
                emit(3, f"FD(file={fd.file_name}{cls_str}) [linha {fd.line}]")
                for rec in fd.records:
                    emit_data_item(4, rec)
        if data_div.working_storage:
            emit(2, "WorkingStorageSection")
            for item in data_div.working_storage:
                emit_data_item(3, item)

    if program.procedure_division:
        proc_div = program.procedure_division
        emit(1, f"ProcedureDivision [linha {proc_div.line}]")
        for sent in proc_div.sentences:
            emit_sentence(2, sent)
        for para in proc_div.paragraphs:
            emit_paragraph(2, para)
        for sec in proc_div.sections:
            emit(2, f"ProcedureSection(name={sec.name}) [linha {sec.line}]")
            for sent in sec.sentences:
                emit_sentence(3, sent)
            for para in sec.paragraphs:
                emit_paragraph(3, para)

    return "\n".join(lines)


# ==============================================================================
# FUNÇÃO PÚBLICA E PONTO DE ENTRADA (CLI)
# ==============================================================================


def parse(
    token_stream: list[Token], errors: list[CompileError] | None = None
) -> tuple[ProgramNode, list[CompileError]]:
    parser = Parser(token_stream, errors)
    ast = parser.parse_program()
    return ast, parser.errors


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python parser.py <arquivo_fonte.cob> [--debug]")
        sys.exit(1)

    caminho_arquivo = sys.argv[1]
    modo_depuracao = "--debug" in sys.argv[2:]

    try:
        with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
            linhas_do_fonte = arquivo.readlines()

        # 1. Análise Léxica e Expansão de Copybooks (via lexer.py)
        stream_de_tokens, erros = make_token_stream(linhas_do_fonte, modo_depuracao)
        diretorio_base = os.path.dirname(os.path.abspath(caminho_arquivo))
        stream_de_tokens = expand_copybooks(
            stream_de_tokens, erros, diretorio_base, modo_depuracao
        )

        # 2. Análise Sintática (Parser)
        arvore_sintatica, erros = parse(stream_de_tokens, erros)

        # 3. Impressão da Árvore Sintática Abstrata (AST)
        print("ÁRVORE SINTÁTICA ABSTRATA (AST)")
        print("=" * 70)
        print(format_ast(arvore_sintatica))

        # 4. Impressão dos Diagnósticos (Léxicos + Sintáticos)
        if erros:
            print()
            print(f"{'SEV':<5} | {'LINHA':<7} | {'COL':<5} | {'MENSAGEM'}")
            print("-" * 70)
            for erro in erros:
                print(f"{erro.severity:<5} | {erro.line:<7} | {erro.col:<5} | {erro.message}")

        # Erros de nível E ou S fazem a compilação falhar (Seção 6)
        if any(erro.severity in ("E", "S") for erro in erros):
            sys.exit(1)

    except FileNotFoundError:
        print(f"Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
        sys.exit(1)
