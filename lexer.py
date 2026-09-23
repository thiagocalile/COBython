import re

user_identifier_regex = re.compile(r"""
^
  ([A-Z]|[0-9])+    # Começa com um ou mais caracteres alfanuméricos
  ([A-Z]|[0-9]|-)*  # Mas pode ter um hífen depois do primeiro
  ([A-Z]|[0-9])?    # E obriga a fechar com um alfanumérico (sem o hífen)
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

for line in source_file:
    sequence_number_area = line[0:6]
    indicator_area = line[6]
    a_area = line[7:11]
    b_area = line[11:72]
    sequence_area = line[72:]

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
