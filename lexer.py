# Integrantes do grupo 9:
#   Thiago Costa    - Nº USP 14609529
#   Oliver Kuramae  - Nº USP 14601326
#   Theo Brito      - Nº USP 13688367

import os
import re
import sys
from typing import NamedTuple

class Token(NamedTuple):
    line: int
    col: int
    area: str
    type: str
    value: str

class CompileError(NamedTuple):
    severity: str   # I, W, E ou S (níveis da seção 6 da especificação)
    line: int
    col: int
    message: str

user_identifier_regex = re.compile(r"""
^
  (?=[A-Z0-9\-]*[A-Z])  # Exige ao menos uma letra em algum ponto da palavra
  [A-Z0-9]        # Começa com um ou mais caracteres alfanuméricos
  (
    [A-Z0-9\-]*   # Mas pode ter um hífen depois do primeiro
    [A-Z0-9]
  )?              # E obriga a fechar com um alfanumérico (sem o hífen)
$
""", re.VERBOSE)

number_regex = re.compile(r"""
^
  (-|\+)?
  [0-9]+
  (\.[0-9]+)?     # Ponto decimal apenas interno (obrigatoriamente seguido de dígitos)
$
""", re.VERBOSE)

KEYWORDS = {
    # Tabela 2.2 da especificação
    "IDENTIFICATION", "ENVIRONMENT", "DATA", "PROCEDURE", "DIVISION", "SECTION",
    "DISPLAY", "ACCEPT", "PERFORM", "STOP", "RUN", "IF", "ELSE", "MOVE",
    # Usadas na estrutura das divisões (seção 3)
    "PROGRAM-ID", "CONFIGURATION", "SOURCE-COMPUTER", "OBJECT-COMPUTER",
    "INPUT-OUTPUT", "FILE-CONTROL", "SELECT", "ASSIGN", "FILE", "FD",
    "WORKING-STORAGE", "PIC", "PICTURE", "VALUE", "IS",
    # Usadas nas verificações semânticas (seção 4) e diretivas (seção 6)
    "TO", "OF", "GO", "OPEN", "READ", "WRITE", "CLOSE", "INPUT", "OUTPUT", "COPY",
    # Parágrafos opcionais da IDENTIFICATION DIVISION (seguidos de texto livre)
    "AUTHOR", "INSTALLATION", "DATE-WRITTEN", "DATE-COMPILED", "SECURITY", "REMARKS",
}

# Parágrafos cujo conteúdo é um comment-entry (texto livre, não tokenizado)
COMMENT_ENTRY_PARAGRAPHS = {
    "AUTHOR", "INSTALLATION", "DATE-WRITTEN", "DATE-COMPILED", "SECURITY", "REMARKS",
}

# Tipos de token (além das palavras-chave) que podem ser continuados na linha seguinte
WORD_TYPES = {"VALID_ID", "NUMBER", "ERROR_INVALID_ID", "ERROR_ID_TOO_BIG"}

# Caracteres que encerram uma palavra fora de um literal
WORD_DELIMITERS = ("'", '"', ",", ";", "(", ")")

ERROR_MESSAGES = {
    "ERROR_ID_TOO_BIG": "Identificador excede o limite de 30 caracteres",
    "ERROR_INVALID_ID": "Palavra inválida",
}

def make_token_stream(source_file: list[str], debugging_mode: bool = False) -> tuple[list[Token], list[CompileError]]:
    token_stream: list[Token] = []
    errors: list[CompileError] = []

    # Variáveis de estado para literais alfanuméricos (strings)
    in_string_literal = False
    string_delimiter = ""
    current_string_value = ""
    string_start_line = 0
    string_start_col = 0
    string_start_area = ""

    # Variável de estado para a character-string que segue PIC/PICTURE
    expect_pic_string = False

    # Variáveis de estado para o texto livre de AUTHOR, REMARKS, etc. (comment-entry)
    expect_comment_entry = False
    in_comment_entry = False

    # Palavra da linha anterior que será colada à primeira palavra da linha de continuação
    continued_word: Token | None = None

    for line_index, raw_line in enumerate(source_file):
        line_number = line_index + 1
        line = raw_line.rstrip("\n\r")

        # Tabs quebram a contagem de colunas do formato fixo, então são expandidos
        if "\t" in line:
            errors.append(CompileError(
                "W", line_number, line.index("\t") + 1,
                "Tabulação expandida para espaços; as colunas podem não corresponder ao esperado"
            ))
            line = line.expandtabs(8)

        # Preencher a linha com espaços até 72 caracteres para evitar IndexErrors
        # A Área do Programa (73-80) é descartada automaticamente pelo fatiamento
        line = line.ljust(72, " ")

        indicator = line[6]
        indicator_type = tokenize_indicator(indicator)

        # 1. Tratar Linhas de Comentário e Depuração
        if indicator_type == "COMMENT_INDICATOR":
            continue # Ignora a linha inteira
        if indicator_type == "DEBUGGING_LINE" and not debugging_mode:
            continue # Linha de depuração só é compilada com o modo de depuração ativo
        if indicator_type == "":
            # Erro: caractere desconhecido na Área de Indicador (a linha ainda é analisada)
            errors.append(CompileError("E", line_number, 7, f"Indicador inválido na coluna 7: '{indicator}'"))

        # O comment-entry continua nas linhas seguintes enquanto a Área A estiver em branco
        if in_comment_entry:
            if line[7:11].strip() == "":
                comment_token = make_comment_entry(line, line_number, 11)
                if comment_token is not None:
                    token_stream.append(comment_token)
                continue
            in_comment_entry = False

        # 2. Tratar Continuação de Literal Alfanumérico
        scan_start = 7
        if in_string_literal:
            if indicator_type == "LITERAL_CONT":
                # O literal continua nesta linha: o primeiro caractere não-branco da Área B
                # deve ser o delimitador, e o literal retoma logo após ele
                j = 11
                while j < 72 and line[j] == " ":
                    j += 1
                if j < 72 and line[j] == string_delimiter:
                    scan_start = j + 1
                else:
                    # Erro: a linha de continuação não tem a aspa de reabertura
                    errors.append(CompileError(
                        "E", line_number, 7,
                        "Linha de continuação sem o delimitador de reabertura do literal"
                    ))
                    token_stream.append(Token(
                        string_start_line, string_start_col, string_start_area,
                        "ERROR_UNCLOSED_STRING", current_string_value
                    ))
                    in_string_literal = False
                    current_string_value = ""
                    string_delimiter = ""
            else:
                # Erro: a string estava aberta, mas a linha atual não tem o indicador de continuação
                errors.append(CompileError(
                    "E", string_start_line, string_start_col, "Literal alfanumérico não fechado"
                ))
                token_stream.append(Token(
                    string_start_line, string_start_col, string_start_area,
                    "ERROR_UNCLOSED_STRING", current_string_value
                ))
                in_string_literal = False
                current_string_value = ""
                string_delimiter = ""
        elif indicator_type == "LITERAL_CONT":
            # Continuação de palavra: o primeiro caractere não-branco da Área B
            # é colado ao último caractere da palavra da linha anterior
            # (a busca começa na Área A, pois compiladores como o GnuCOBOL também a aceitam)
            j = 7
            while j < 72 and line[j] == " ":
                j += 1
            last_token = token_stream[-1] if token_stream else None
            if (last_token is not None
                    and (last_token.type in KEYWORDS or last_token.type in WORD_TYPES)
                    and j < 72 and line[j] not in WORD_DELIMITERS and line[j] != "."):
                continued_word = token_stream.pop()
                # Descarta o diagnóstico da palavra incompleta, que será reavaliada após a junção
                if continued_word.type in ERROR_MESSAGES:
                    errors = [e for e in errors if (e.line, e.col) != (continued_word.line, continued_word.col)]
                scan_start = j
            else:
                # Aviso: indicador de continuação sem literal ou palavra para continuar
                errors.append(CompileError(
                    "W", line_number, 7, "Indicador de continuação sem literal alfanumérico ou palavra para continuar"
                ))

        # 3. Varredura das colunas 8 a 72 (índices 7 a 71 em Python)
        i = scan_start
        while i < 72:
            char = line[i]
            col_actual = i + 1 # Coluna baseada em 1 para o programador
            area = "AREA_A" if col_actual <= 11 else "AREA_B"

            # --- ESTADO: DENTRO DE UMA STRING ---
            if in_string_literal:
                if char == string_delimiter:
                    # Duas aspas seguidas dentro do literal representam uma aspa literal
                    if i + 1 < 72 and line[i + 1] == string_delimiter:
                        current_string_value += char
                        i += 2
                        continue
                    # Encontrou o fecho da string
                    token_stream.append(Token(
                        string_start_line, string_start_col, string_start_area,
                        "ALPHANUMERIC_LITERAL", current_string_value
                    ))
                    in_string_literal = False
                    current_string_value = ""
                    string_delimiter = ""
                else:
                    current_string_value += char
                i += 1
                continue

            # --- ESTADO: FORA DE UMA STRING ---
            if char.isspace():
                i += 1
                continue

            # Início de um Literal Alfanumérico
            if char in ("'", '"'):
                in_string_literal = True
                string_delimiter = char
                current_string_value = ""
                string_start_line = line_number
                string_start_col = col_actual
                string_start_area = area
                i += 1
                continue

            # Pontuação isolada (vírgula, parênteses, ponto e vírgula)
            if char in (",", ";", "(", ")"):
                token_type = match_token(char)
                token_stream.append(Token(line_number, col_actual, area, token_type, char))
                i += 1
                continue

            # Consumo de Palavra (Keywords, Identifiers, Numbers)
            # Lê todos os caracteres até encontrar um espaço ou delimitador
            start_i = i
            word = ""
            if expect_pic_string:
                # Character-string do PICTURE: lê até o espaço, mantendo ( ) , e o ponto decimal interno
                while i < 72 and not line[i].isspace():
                    word += line[i]
                    i += 1
            else:
                while i < 72 and not line[i].isspace() and line[i] not in WORD_DELIMITERS:
                    word += line[i]
                    i += 1

            # COBOL não diferencia maiúsculas de minúsculas fora dos literais
            word = word.upper()

            # Tratamento Crítico do Ponto Final (.)
            # O ponto pode ser o fim de uma sentença ou casa decimal de um número.
            has_trailing_dot = False
            if len(word) > 1 and word.endswith("."):
                # Remove o ponto final da palavra para o analisar separadamente
                word = word[:-1]
                has_trailing_dot = True
            elif word == ".":
                # A palavra é estritamente apenas um ponto isolado
                word = ""
                has_trailing_dot = True

            # Valida e emite a palavra extraída
            if word:
                word_line = line_number
                word_col = start_i + 1
                if continued_word is not None:
                    # Junta com a palavra da linha anterior, mantendo a posição de início dela
                    word = continued_word.value + word
                    word_line = continued_word.line
                    word_col = continued_word.col
                    continued_word = None
                word_area = "AREA_A" if word_col <= 11 else "AREA_B"
                if expect_pic_string and word != "IS":
                    # Emite a character-string do PICTURE como um token único
                    token_type = "PIC_STRING"
                    expect_pic_string = False
                else:
                    token_type = match_token(word)
                    # Após PIC/PICTURE (e o IS opcional), a próxima palavra é a character-string
                    if token_type in ("PIC", "PICTURE"):
                        expect_pic_string = True
                # Após AUTHOR, REMARKS, etc., o que vem depois do ponto é texto livre
                expect_comment_entry = token_type in COMMENT_ENTRY_PARAGRAPHS
                token_stream.append(Token(word_line, word_col, word_area, token_type, word))
                if token_type in ERROR_MESSAGES:
                    errors.append(CompileError("E", word_line, word_col, f"{ERROR_MESSAGES[token_type]}: '{word}'"))

            # Emite o ponto final como um token isolado
            if has_trailing_dot:
                dot_col = i # O ponto é sempre o último caractere lido
                dot_area = "AREA_A" if dot_col <= 11 else "AREA_B"
                token_stream.append(Token(line_number, dot_col, dot_area, "DOT", "."))

                # Emite o resto da linha como comment-entry, sem tokenizar
                if expect_comment_entry:
                    comment_token = make_comment_entry(line, line_number, i)
                    if comment_token is not None:
                        token_stream.append(comment_token)
                    expect_comment_entry = False
                    in_comment_entry = True
                    i = 72

    # Proteção de fim de ficheiro: verifica se o código acabou com uma string aberta
    if in_string_literal:
        errors.append(CompileError(
            "E", string_start_line, string_start_col, "Literal alfanumérico não fechado"
        ))
        token_stream.append(Token(
            string_start_line, string_start_col, string_start_area,
            "ERROR_UNCLOSED_STRING", current_string_value
        ))

    return token_stream, errors

def make_comment_entry(line: str, line_number: int, start: int) -> Token | None:
    # Texto livre a partir do índice start até a coluna 72 (mantém maiúsculas e minúsculas)
    comment_text = line[start:72].strip()
    if not comment_text:
        return None
    comment_col = line.index(comment_text, start) + 1
    comment_area = "AREA_A" if comment_col <= 11 else "AREA_B"
    return Token(line_number, comment_col, comment_area, "COMMENT_ENTRY", comment_text)

def find_copybook(base_dir: str, name: str) -> str | None:
    # Procura o copybook pelo nome exato ou em minúsculas, com as extensões usuais
    for candidate in (name, name.lower()):
        for extension in ("", ".cpy", ".CPY", ".cob", ".COB"):
            path = os.path.join(base_dir, candidate + extension)
            if os.path.isfile(path):
                return path
    return None

def expand_copybooks(token_stream: list[Token], errors: list[CompileError], base_dir: str,
                     debugging_mode: bool = False, open_copybooks: frozenset[str] = frozenset()) -> list[Token]:
    # Diretiva COPY [Nome] . : substitui a diretiva pelos tokens do copybook
    # As linhas dos tokens incluídos são relativas ao próprio copybook
    expanded: list[Token] = []
    i = 0
    while i < len(token_stream):
        token = token_stream[i]
        if token.type == "COPY":
            if (i + 2 < len(token_stream)
                    and token_stream[i + 1].type in ("VALID_ID", "ALPHANUMERIC_LITERAL")
                    and token_stream[i + 2].type == "DOT"):
                name = token_stream[i + 1].value.strip()
                copybook_path = find_copybook(base_dir, name)
                if copybook_path is None:
                    errors.append(CompileError("E", token.line, token.col, f"Copybook '{name}' não encontrado"))
                elif copybook_path in open_copybooks:
                    # Falha catastrófica: o copybook inclui a si mesmo (direta ou indiretamente)
                    errors.append(CompileError("S", token.line, token.col, f"Inclusão recursiva do copybook '{name}'"))
                else:
                    with open(copybook_path, "r", encoding="utf-8") as copybook:
                        copy_tokens, copy_errors = make_token_stream(copybook.readlines(), debugging_mode)
                    errors.extend(copy_errors)
                    # O próprio copybook também pode conter diretivas COPY
                    expanded.extend(expand_copybooks(
                        copy_tokens, errors, os.path.dirname(copybook_path),
                        debugging_mode, open_copybooks | {copybook_path}
                    ))
                i += 3
                continue
            # Erro: COPY sem nome ou sem o ponto final
            errors.append(CompileError("E", token.line, token.col, "Diretiva COPY mal formada (esperado: COPY [Nome].)"))
        expanded.append(token)
        i += 1
    return expanded

def match_token(substring: str) -> str:
    # 1. Tenta casar com pontuação (.,;)
    token_type = match_punctuation(substring)
    if token_type != "":
        return token_type

    # 2. Tenta casar com palavras-chave reservadas
    token_type = match_keyword(substring)
    if token_type != "":
        return token_type

    # 3. Tenta casar com operadores aritméticos e relacionais
    token_type = match_operator(substring)
    if token_type != "":
        return token_type

    # 4. Tenta casar com literais numéricos
    token_type = match_number(substring)
    if token_type != "":
        return token_type

    # 5. Por fim, classifica como identificador (válido ou com erro de formação)
    return match_identifier(substring)

def match_punctuation(substring: str) -> str:
    match substring:
        case ".":
            return "DOT"
        case ",":
            return "COMMA"
        case ";":
            return "SEMICOLON"
        case "(":
            return "LEFT_PAREN"
        case ")":
            return "RIGHT_PAREN"
        case _:
            return ""

def match_operator(substring: str) -> str:
    match substring:
        case ">":
            return "GREATER_THAN"
        case "<":
            return "LESS_THAN"
        case "=":
            return "EQUAL"
        case ">=":
            return "GREATER_EQUAL"
        case "<=":
            return "LESS_EQUAL"
        case "+":
            return "PLUS"
        case "-":
            return "MINUS"
        case "*":
            return "MULTIPLY"
        case "/":
            return "DIVIDE"
        case "**":
            return "POWER"
        case _:
            return ""

def match_number(substring: str) -> str:
    if(number_regex.match(substring)):
        return "NUMBER"
    else:
        return ""

def match_identifier(substring: str) -> str:
    if(len(substring) > 30):
        return "ERROR_ID_TOO_BIG"
    else:
        if(user_identifier_regex.match(substring)):
            return "VALID_ID"
        else:
            return "ERROR_INVALID_ID"

def match_keyword(substring: str) -> str:
    # O tipo do token de uma palavra-chave é a própria palavra
    if substring in KEYWORDS:
        return substring
    return ""

def tokenize_indicator(indicator: str) -> str:
    match indicator:
        case " ":
            return "CODE_LINE"
        case "*" | "/":
            return "COMMENT_INDICATOR"
        case "-":
            return "LITERAL_CONT"
        case "D" | "d":
            return "DEBUGGING_LINE"
        case _:
            return ""


if __name__ == "__main__":
    # Verifica se o usuário passou o nome do arquivo no terminal
    if len(sys.argv) < 2:
        print("Uso: python lexer.py <arquivo_fonte.cob> [--debug]")
        sys.exit(1)

    caminho_arquivo = sys.argv[1]
    # Com --debug, as linhas com indicador D são compiladas
    modo_depuracao = "--debug" in sys.argv[2:]

    try:
        # Lê o arquivo e passa as linhas para a função
        with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
            linhas_do_fonte = arquivo.readlines()

        # Gera o stream de tokens e expande as diretivas COPY
        stream_de_tokens, erros = make_token_stream(linhas_do_fonte, modo_depuracao)
        diretorio_base = os.path.dirname(os.path.abspath(caminho_arquivo))
        stream_de_tokens = expand_copybooks(stream_de_tokens, erros, diretorio_base, modo_depuracao)

        # Imprime o resultado formatado
        print(f"{'LINHA':<7} | {'COL':<5} | {'ÁREA':<10} | {'TIPO DO TOKEN':<25} | {'VALOR'}")
        print("-" * 70)
        for token in stream_de_tokens:
            linha, col, area, tipo, valor = token
            print(f"{linha:<7} | {col:<5} | {area:<10} | {tipo:<25} | {valor}")

        # Imprime os diagnósticos com o nível de severidade
        if erros:
            print()
            print(f"{'SEV':<5} | {'LINHA':<7} | {'COL':<5} | {'MENSAGEM'}")
            print("-" * 70)
            for erro in erros:
                print(f"{erro.severity:<5} | {erro.line:<7} | {erro.col:<5} | {erro.message}")

        # Erros de nível E ou S fazem a compilação falhar
        if any(erro.severity in ("E", "S") for erro in erros):
            sys.exit(1)

    except FileNotFoundError:
        print(f"Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
