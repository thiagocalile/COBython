import re
import sys

user_identifier_regex = re.compile(r"""
^
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
  \.?
  [0-9]*
$
""", re.VERBOSE)

def make_token_stream(source_file: list[str]) -> list[tuple[int, int, str, str, str]]:
    token_stream: list[tuple[int, int, str, str, str]] = []
    
    # Variáveis de estado para literais alfanuméricos (strings)
    in_string_literal = False
    string_delimiter = ""
    current_string_value = ""
    string_start_line = 0
    string_start_col = 0
    string_start_area = ""

    for line_index, raw_line in enumerate(source_file):
        line_number = line_index + 1
        
        # Preencher a linha com espaços até 72 caracteres para evitar IndexErrors
        # A Área do Programa (73-80) é descartada automaticamente pelo fatiamento
        line = raw_line.rstrip("\n\r").ljust(72, " ")
        
        indicator = line[6] if len(line) > 6 else " "
        
        # 1. Tratar Linhas de Comentário e Depuração
        if indicator in ("*", "/"):
            continue # Ignora a linha inteira
            
        # 2. Tratar Continuação de Literal Alfanumérico
        if in_string_literal:
            if indicator == "-":
                # O literal continua nesta linha. 
                # (A especificação não detalha, mas normalmente retoma-se na Área B)
                pass 
            else:
                # Erro: a string estava aberta, mas a linha atual não tem o indicador de continuação
                token_stream.append((
                    string_start_line, string_start_col, string_start_area, 
                    "ERROR_UNCLOSED_STRING", current_string_value
                ))
                in_string_literal = False
                current_string_value = ""
        
        # 3. Varredura das colunas 8 a 72 (índices 7 a 71 em Python)
        i = 7
        while i < 72 and i < len(line):
            char = line[i]
            col_actual = i + 1 # Coluna baseada em 1 para o programador
            area = "AREA_A" if col_actual <= 11 else "AREA_B"
            
            # --- ESTADO: DENTRO DE UMA STRING ---
            if in_string_literal:
                if char == string_delimiter:
                    # Encontrou o fecho da string
                    token_stream.append((
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
                token_stream.append((line_number, col_actual, area, token_type, char))
                i += 1
                continue
                
            # Consumo de Palavra (Keywords, Identifiers, Numbers)
            # Lê todos os caracteres até encontrar um espaço ou delimitador
            start_i = i
            word = ""
            while i < 72 and not line[i].isspace() and line[i] not in ("'", '"', ",", ";", "(", ")"):
                word += line[i]
                i += 1
                
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
                word_col = start_i + 1
                word_area = "AREA_A" if word_col <= 11 else "AREA_B"
                token_stream.append((line_number, word_col, word_area, match_token(word), word))
                
            # Emite o ponto final como um token isolado
            if has_trailing_dot:
                dot_col = (start_i + len(word) + 1) if word else (start_i + 1)
                dot_area = "AREA_A" if dot_col <= 11 else "AREA_B"
                token_stream.append((line_number, dot_col, dot_area, "DOT", "."))

    # Proteção de fim de ficheiro: verifica se o código acabou com uma string aberta
    if in_string_literal:
        token_stream.append((
            string_start_line, string_start_col, string_start_area, 
            "ERROR_UNCLOSED_STRING", current_string_value
        ))
        
    return token_stream

def match_token(substring: str) -> str:
    # 1. Tenta casar com pontuação (.,;)
    token_type = match_punctuation(substring)
    if token_type != "":
        return token_type
        
    # 2. Tenta casar com delimitadores isolados (' ou ")
    token_type = match_delimiter(substring)
    if token_type != "":
        return token_type
        
    # 3. Tenta casar com palavras-chave reservadas
    token_type = match_keyword(substring)
    if token_type != "":
        return token_type
        
    # 4. Tenta casar com literais numéricos
    token_type = match_number(substring)
    if token_type != "":
        return token_type
        
    # 5. Tenta casar com identificadores (variáveis, parágrafos)
    token_type = match_identifier(substring)
    if token_type != "":
        return token_type
        
    # Se esgotou todas as opções e não deu match
    return "ERROR_UNKNOWN_TOKEN"

def match_delimiter(substring: str) -> str:
    match substring:
        case r'"':
            return "DOUBLE_QUOTE"
        case r"'":
            return "SINGLE_QUOTE"
        case _:
            return ""

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
    match substring:
        case "IDENTIFICATION":
            return "IDENTIFICATION"
        case "ENVIRONMENT":
            return "ENVIRONMENT"
        case "DATA":
            return "DATA"
        case "PROCEDURE":
            return "PROCEDURE"
        case "DIVISION":
            return "DIVISION"
        case "SECTION":
            return "SECTION"
        case "DISPLAY":
            return "DISPLAY"
        case "ACCEPT":
            return "ACCEPT"
        case "PERFORM":
            return "PERFORM"
        case "STOP":
            return "STOP"
        case "RUN":
            return "RUN"
        case "IF":
            return "IF"
        case "ELSE":
            return "ELSE"
        case "MOVE":
            return "MOVE"
        case _:
            return ""
    
def tokenize_indicator(indicator: str) -> str:
    match indicator:
        case "*" | "/":
            return "COMMENT_INDICATOR"
        case "-":
            return "LITERAL_CONT"
        case "D":
            return "DEBUGGING_LINE"
        case _:
            return ""


if __name__ == "__main__":
    # Verifica se o usuário passou o nome do arquivo no terminal
    if len(sys.argv) < 2:
        print("Uso: python lexer.py <arquivo_fonte.cob>")
        sys.exit(1)

    caminho_arquivo = sys.argv[1]

    try:
        # Lê o arquivo e passa as linhas para a função
        with open(caminho_arquivo, "r", encoding="utf-8") as arquivo:
            linhas_do_fonte = arquivo.readlines()
        
        # Gera o stream de tokens
        stream_de_tokens = make_token_stream(linhas_do_fonte)
        
        # Imprime o resultado formatado
        print(f"{'LINHA':<7} | {'COL':<5} | {'ÁREA':<10} | {'TIPO DO TOKEN':<25} | {'VALOR'}")
        print("-" * 70)
        for token in stream_de_tokens:
            linha, col, area, tipo, valor = token
            print(f"{linha:<7} | {col:<5} | {area:<10} | {tipo:<25} | {valor}")
            
    except FileNotFoundError:
        print(f"Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
