# Walkthrough: Analisador Sintático (`parser.py`)

## Alterações Realizadas

- **[NEW] [parser.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/parser.py)**:
  - Integração direta com [lexer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/lexer.py) (`Token`, `CompileError`, `make_token_stream`, `expand_copybooks`).
  - Construção da **Árvore Sintática Abstrata (AST)** completa em nós `dataclass` (`ProgramNode`, `IdentificationDivisionNode`, `EnvironmentDivisionNode`, `DataDivisionNode`, `ProcedureDivisionNode`, seções, parágrafos, sentenças, instruções e expressões).
  - Validação da gramática e hierarquia da **Parte 3 da [ESPECIFICAÇÃO TEXTUAL.txt](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/ESPECIFICAÇÃO%20TEXTUAL.txt)**:
    - Ordem estrita de derivação: `<IdentificationDivision> [<EnvironmentDivision>] [<DataDivision>] <ProcedureDivision> [END PROGRAM <Id>.]`.
    - `IDENTIFICATION DIVISION`: obrigatoriedade de `PROGRAM-ID. [Nome].` e suporte aos parágrafos informativos (`AUTHOR`, `INSTALLATION`, `DATE-WRITTEN`, `DATE-COMPILED`, `SECURITY`, `REMARKS`).
    - `ENVIRONMENT DIVISION`: `CONFIGURATION SECTION` (`SOURCE-COMPUTER`, `OBJECT-COMPUTER`, `SPECIAL-NAMES`) e `INPUT-OUTPUT SECTION` (`FILE-CONTROL`, `SELECT ... ASSIGN TO ...` e cláusulas `ORGANIZATION`, `ACCESS MODE`, `RECORD KEY`, `ALTERNATE RECORD KEY`, `FILE STATUS`).
    - `DATA DIVISION`: `FILE SECTION` (`FD` e registros) e `WORKING-STORAGE SECTION`, montagem da árvore hierárquica de níveis (`01`, `02-49`, `77`, `88`), validação da obrigatoriedade de `PIC`/`PICTURE` em itens elementares e suporte às cláusulas `VALUE`/`VALUES` e `OCCURS ... TIMES [INDEXED BY ...]`.
    - `PROCEDURE DIVISION`: seções, parágrafos, sentenças obrigatoriamente finalizadas por ponto (`.`) e suporte a todas as instruções da especificação e dos programas de teste (`DISPLAY`, `ACCEPT`, `MOVE`, `PERFORM`, `STOP RUN`, `GO TO`, `IF/ELSE/END-IF`, `OPEN`, `CLOSE`, `READ`, `WRITE`, `START`, `ADD`, `SUBTRACT`, `MULTIPLY`, `DIVIDE`, `COMPUTE`, `EVALUATE`, `SEARCH`, `SET`, `INSPECT`), incluindo condições relacionais abreviadas (`IF OP = "S" OR "U"`), subscritos (`DPTO(IDX, 1)`), qualificadores (`OF`) e `FUNCTION`.
    - Recuperação de erros (*panic mode*) sincronizada em `.` e cabeçalhos de divisão/seção/parágrafo.

---

## Testes e Validação

### 1. Execução sobre [test.cob](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/test.cob)
Comando executado:
```bash
python3 parser.py test.cob
```
Saída obtida (código de saída `0`):
```text
ÁRVORE SINTÁTICA ABSTRATA (AST)
======================================================================
Program() [linha 2]
  IdentificationDivision(program_id=MEU-PROGRAMA) [linha 2]
  DataDivision [linha 4]
    WorkingStorageSection
      DataItem(level=01, name=REGISTRO) [linha 5]
        DataItem(level=02, name=NOME, pic=X(30)) [linha 6]
      DataItem(level=77, name=VALOR-1, pic=9(04), value=+123.45) [linha 7]
  ProcedureDivision [linha 8]
    ProcedureSection(name=INICIO) [linha 9]
      Sentence [linha 10]
        MoveStmt(from=VALOR-1 -> to=[NOME]) [linha 10]
      Sentence [linha 11]
        IfStmt(condition=VALOR-1) [linha 11]
          THEN:
            DisplayStmt("ESTA STRING FOI                                 QUEBRADA EM DUAS LINHAS.") [linha 12]
          ELSE:
            AcceptStmt(target=NOME(1)) [linha 15]
            StopRunStmt() [linha 16]
```

### 2. Suíte de Programas (`prog001` a `prog007`)
- **Fluxos de tokens válidos** (`resultados_lexer/prog001_divisoes.txt` a `prog007_loja.txt`): todos os 7 programas foram analisados com **0 erros (`E`/`S`)**, gerando suas respectivas ASTs completas (com avisos `W` apenas onde níveis `01`/`77` foram indentados na Área B em `prog002` e `prog003`).
- **Programas com erros** (`resultados_lexer/erros/prog001_divisoes.cob` a `prog007_loja.cob`): todos os erros léxicos e sintáticos (como `IF WKS-SAIR == 1` na linha 36 de `prog003_calculadora.cob`) foram reportados com recuperação limpa sem interromper a análise do restante do programa.

### 3. Casos de Erro Sintático da Seção 3
Foram validados casos específicos para:
- Divisões fora da ordem obrigatória (`DATA DIVISION` antes de `ENVIRONMENT DIVISION`).
- Ausência de `IDENTIFICATION DIVISION` ou `PROCEDURE DIVISION`.
- Ausência de `PROGRAM-ID.` na `IDENTIFICATION DIVISION`.
- Item elementar sem cláusula `PIC`/`PICTURE`.
- Nível de estrutura inválido (ex.: `55`) e subcampo `02` sem `01` pai.
- Sentença na `PROCEDURE DIVISION` sem ponto final (`.`).
- Instruções malformadas (`MOVE` sem `TO`, `STOP` sem `RUN`).
