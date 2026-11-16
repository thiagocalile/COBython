# Walkthrough: Compilador COBOL (`parser.py`, `semantic_analyzer.py` e `llvm_codegen.py`)

## Alterações Realizadas

### 1. Parte 3 — Analisador Sintático ([parser.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/parser.py))
- Construção da **Árvore Sintática Abstrata (AST)** completa a partir do fluxo de tokens do [lexer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/lexer.py).
- Validação da hierarquia das 4 divisões, níveis de estrutura (`01`, `02-49`, `77`, `88`), cláusula `PIC` obrigatória em itens elementares, sentenças encerradas por `.` e recuperação de erros (*panic mode*).

### 2. Parte 4 — Analisador Semântico ([semantic_analyzer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/semantic_analyzer.py))
- Validação das 4 regras da Seção 4 da [ESPECIFICAÇÃO TEXTUAL.txt](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/ESPECIFICAÇÃO%20TEXTUAL.txt):
  1. Compatibilidade de tipos e literais em `MOVE` (impedindo `PIC A` $\to$ `PIC 9` e numéricos $\to$ `PIC A`).
  2. Unicidade de identificadores no mesmo escopo, detecção de variáveis não declaradas e validação de qualificação hierárquica (`OF`).
  3. Validação de parágrafos e seções alvo em `PERFORM` e `GO TO`.
  4. Associação de arquivos entre `SELECT` (`FILE-CONTROL`), `FD` (`DATA DIVISION`) e instruções `OPEN`, `READ`, `WRITE`, `CLOSE` e `START`.

### 3. Parte 5 — Gerador de Código Intermediário LLVM IR ([llvm_codegen.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/llvm_codegen.py))
- Tradução da AST validada para um módulo textual **LLVM IR (`.ll`)** em formato SSA e compilação automática para executável nativo via `clang` (ou `llc` + `gcc`).
- **Layout Contíguo de Memória (`DATA DIVISION`)**:
  - Cada registro `01` ou variável `77` é emitido como um buffer global de bytes (`@var_<NOME> = internal global [N x i8] c"..."`) inicializado com seus valores `VALUE` em tempo de compilação.
  - Subcampos (`02-49`) e tabelas multidimensionais (`OCCURS ... TIMES INDEXED BY ...`) calculam seus deslocamentos em bytes via aritmética SSA e `getelementptr inbounds i8, ptr @var_ROOT, i64 %offset`.
- **Tradução Completa da `PROCEDURE DIVISION`**:
  - Suporte a `DISPLAY` (com e sem `WITH NO ADVANCING`), `ACCEPT`, `MOVE` (incluindo grupos, `LOW-VALUES`, `SPACES`, `ZEROS` e `FUNCTION UPPER-CASE`), `ADD`, `SUBTRACT`, `MULTIPLY`, `DIVIDE` (com `GIVING`, `REMAINDER` e `ON SIZE ERROR`), `COMPUTE` (`**` via `@llvm.pow.f64`), `IF / ELSE / END-IF`, `PERFORM` (`THRU`, `UNTIL`, `TIMES`), `GO TO`, `EVALUATE`, `SEARCH`, `SET`, `INSPECT` (`TALLYING`, `REPLACING`, `CONVERTING`) e I/O de arquivos (`OPEN`, `READ`, `WRITE`, `CLOSE`, `START`).

---

## Testes e Validação da Geração de Código (`.ll` $\to$ Binário Nativo via `clang`)

Todos os programas válidos foram compilados para `.ll`, montados em binários nativos pelo `clang` sem nenhum erro ou *warning*, e executados:

### 1. [test.cob](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/test.cob)
```bash
python3 llvm_codegen.py test.cob && ./test
```
Saída:
```text
[OK] Representação Intermediária LLVM gerada com sucesso em: 'test.ll'
[INFO] Executável nativo compilado via clang: 'test'
ESTA STRING FOI                                 QUEBRADA EM DUAS LINHAS.
```

### 2. Suíte Completa (`prog001` a `prog007`)
| Programa | Recursos Exercitados em LLVM IR | Resultado da Compilação (`clang`) e Execução Nativa |
| :--- | :--- | :--- |
| `prog001_divisoes` | Estrutura das 4 divisões, `DISPLAY`, `STOP RUN` | Compilado e executado (`Hello world` / `Enio Basso - 2`) |
| `prog002_operacoes` | `MOVE` qualificado (`OF`), `ADD`, `SUBTRACT`, `MULTIPLY`, `DIVIDE ... REMAINDER`, `ON SIZE ERROR` | Compilado e executado com todos os resultados aritméticos exatos |
| `prog003_calculadora` | `ACCEPT`, `FUNCTION UPPER-CASE`, `COMPUTE (**)` com decimais `V99` e máscara editada `-ZZZZZZZZZ9.99`, `PERFORM ... UNTIL` | Compilado e executado (`12.5 + 7.25 = 19.75`, `2 ** 8 = 256.00`) |
| `prog004_folha_pgto` | `MOVE` em grupo contíguo de 48 bytes, tabelas 1D e 2D (`OCCURS` + `DPTO(IDX, 2)`), `SET`, `SEARCH ... WHEN` | Compilado e executado, fatiando perfeitamente `JOSEA`, `MARIA`, `FABIO` e encontrando `WHEN NOME(TID) = MARIA` |
| `prog005_strings` | `INSPECT` (`TALLYING FOR CHARACTERS/ALL/LEADING`, `BEFORE/AFTER INITIAL`, `REPLACING FIRST/ALL/CHARACTERS`, `CONVERTING`) | Compilado e executado (`CONT=15`, `03`, `05`, `09`, `YESNO=1`, `'HAPPY WORLD 123'`, `' GOOD-DAY      '`, `'hello world    '`) |
| `prog006_files` | `SELECT`, `FD`, `OPEN OUTPUT`, `WRITE`, `CLOSE` | Compilado e executado, gravando os registros em `DADOS.TXT` |
| `prog007_loja` | `FILE STATUS`, `ASSIGN TO` variável de grupo (`PROG10-001.DAT`), `EVALUATE ... WHEN`, `READ`, `WRITE`, `START` | Compilado e executado com criação de arquivos e navegação no menu |
