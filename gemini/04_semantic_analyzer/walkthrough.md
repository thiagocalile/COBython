# Walkthrough: Analisador Sintático (`parser.py`) e Analisador Semântico (`semantic_analyzer.py`)

## Alterações Realizadas

### 1. Parte 3 — Analisador Sintático ([parser.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/parser.py))
- Integração com [lexer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/lexer.py) (`Token`, `CompileError`, `make_token_stream`, `expand_copybooks`).
- Construção da **Árvore Sintática Abstrata (AST)** completa (`ProgramNode`, `IdentificationDivisionNode`, `EnvironmentDivisionNode`, `DataDivisionNode`, `ProcedureDivisionNode`, seções, parágrafos, sentenças, instruções e expressões).
- Validação da gramática da **Seção 3 da [ESPECIFICAÇÃO TEXTUAL.txt](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/ESPECIFICAÇÃO%20TEXTUAL.txt)** e recuperação de erros (*panic mode*).

### 2. Parte 4 — Analisador Semântico ([semantic_analyzer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/semantic_analyzer.py))
- Integração com [lexer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/lexer.py) e [parser.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/parser.py), expondo a função `analyze(program, errors) -> tuple[bool, list[CompileError]]`.
- Construção da Tabela de Símbolos com hierarquia de grupos (`ancestors`), escopo local/global, índices `INDEXED BY`, arquivos `SELECT` / `FD` e parágrafos/seções da `PROCEDURE DIVISION`.
- Implementação das 4 verificações da **Seção 4 da [ESPECIFICAÇÃO TEXTUAL.txt](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/ESPECIFICAÇÃO%20TEXTUAL.txt)**:
  1. **Compatibilidade de `MOVE`**: bloqueia movimentação de `PIC A` para `PIC 9`, de dados numéricos (`PIC 9` / literais numéricos) para `PIC A`, e de literais alfanuméricos não numéricos para `PIC 9` (incluindo verificação de `VALUE` inicial).
  2. **Unicidade de Identificadores e Qualificação (`OF`)**: impede variáveis duplicadas no mesmo escopo, detecta variáveis não declaradas na `PROCEDURE DIVISION`, exige qualificação `OF` quando um nome existe em múltiplos grupos e valida a cadeia hierárquica do `OF`.
  3. **Validação de Destinos de Controle**: valida se os alvos de `PERFORM` (incluindo `THRU`) e `GO TO` foram explicitamente declarados como parágrafos ou seções na `PROCEDURE DIVISION`.
  4. **Associação de Arquivos**: valida a correspondência mútua entre `SELECT` (`FILE-CONTROL`) e `FD` (`DATA DIVISION`), exigindo ambos para qualquer operação `OPEN`, `READ`, `WRITE`, `CLOSE` e `START`.

---

## Resultados dos Testes e Validação

### 1. Execução sobre [test.cob](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/test.cob)
```bash
python3 semantic_analyzer.py test.cob
```
Saída (código de retorno `0`):
```text
[OK] VALIDAÇÃO CONCLUÍDA COM SUCESSO (0 erros, 0 aviso(s))
```

### 2. Suíte de Programas (`prog001` a `prog007`)
- **Programas válidos** (`prog001_divisoes` a `prog007_loja`): todos os 7 retornaram `valid = True` (`0` erros `E`/`S`), comprovando o suporte a `OCCURS ... INDEXED BY`, qualificações `OF`, `SELECT`/`FD` e `PERFORM`.
- **Programas com erros** (`resultados_lexer/erros/prog001_divisoes.cob` a `prog007_loja.cob`): todos retornaram `valid = False`, reportando tanto os erros léxicos/sintáticos quanto os erros semânticos decorrentes (como `WRK-VALOR1`, `CONT`, `IDX` e `WS-OPCAO` não declaradas).

### 3. Testes Unitários das 4 Regras Semânticas (Seção 4)
| Regra | Cenário Testado | Resultado |
| :--- | :--- | :--- |
| **1. Compatibilidade de MOVE** | `MOVE VAR-ALFA (PIC A) TO VAR-NUM (PIC 9)`, `MOVE "TEXTO" TO VAR-NUM`, `MOVE 12345 TO VAR-ALFA` | Reprovado (`valid=False`) com 3 erros `E` detalhados |
| **2a. Unicidade no mesmo escopo** | Duas variáveis `77 VAR-DUP` e dois subcampos `02 CAMPO-A` no mesmo `01 GRUPO-1` | Reprovado (`valid=False`) apontando a linha da declaração anterior |
| **2b. Ambiguidade sem `OF`** | `02 NOME` em `01 GRUPO-A` e `01 GRUPO-B`, referenciado como `DISPLAY NOME.` | Reprovado (`valid=False`) exigindo qualificação `OF` |
| **2c. Qualificação com `OF`** | `MOVE "JOAO" TO NOME OF GRUPO-A.` e `DISPLAY NOME OF GRUPO-B.` | Aprovado (`valid=True`, `0` erros) |
| **3. Destinos de Controle** | `PERFORM PARAGRAFO-FANTASMA.` e `GO TO DESTINO-INEXISTENTE.` | Reprovado (`valid=False`) identificando ambos os destinos ausentes |
| **4. Associação de Arquivos** | `OPEN`, `READ`, `WRITE`, `CLOSE` sem `SELECT` no `FILE-CONTROL` e/ou sem `FD` na `DATA DIVISION` | Reprovado (`valid=False`) detalhando se falta `SELECT`, `FD` ou ambos |
