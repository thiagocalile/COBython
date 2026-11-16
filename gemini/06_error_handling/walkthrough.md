# Walkthrough: Compilador COBython Completo (`cobython.py` e Seção 6)

## Visão Geral do Pipeline Implementado

1. **Parte 2 — Analisador Léxico ([lexer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/lexer.py))**:
   - Tokenização em formato fixo (colunas 1–80, Áreas A e B, indicador na coluna 7) e expansão recursiva da diretiva `COPY`.
2. **Parte 3 — Analisador Sintático ([parser.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/parser.py))**:
   - Construção da Árvore Sintática Abstrata (AST) e validação da hierarquia das 4 divisões, níveis de dados (`01`, `02-49`, `77`, `88`), cláusula `PIC` e instruções da `PROCEDURE DIVISION`.
3. **Parte 4 — Analisador Semântico ([semantic_analyzer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/semantic_analyzer.py))**:
   - Verificação das 4 regras da Seção 4 (compatibilidade de `MOVE`, unicidade e qualificação `OF`, destinos de `PERFORM`/`GO TO`, associação de arquivos `SELECT`/`FD`), além de diagnósticos de otimização e estilo (níveis `I` e `W`).
4. **Parte 5 — Gerador de Representação Intermediária LLVM ([llvm_codegen.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/llvm_codegen.py))**:
   - Emissão de LLVM IR (`.ll`) em formato SSA e integração com `clang` / `opt` / `llc`.
5. **Parte 6 — Driver de Linha de Comando Estilo GCC/Clang e Notificação de Erros ([cobython.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/cobython.py))**:
   - Interface unificada de usuário com repasse de flags de otimização (`-O0` a `-O3`, `-Os`, `-Oz`), controle de estágios (`-fsyntax-only`, `-S`, `-c`, `-emit-llvm`, `-save-temps`, `-g`, `-I`), arquitetura de backends plugáveis (`--backend`) e formatação de diagnósticos nos 4 níveis da Seção 6 (`I`, `W`, `E`, `S`).

---

## Validação do `cobython.py`

### 1. Compilação com Otimização (`-O3`) e Modo Verboso (`-v`)
```bash
python3 cobython.py test.cob -O3 -v -o test_o3 && ./test_o3
```
Saída:
```text
[COBython] 1/4 Executando Análise Léxica e COPY em 'test.cob'...
[COBython] 2/4 Executando Análise Sintática (parser.py)...
[COBython] 3/4 Executando Análise Semântica (semantic_analyzer.py)...
test.cob:11:12: [I - INFORMATIONAL] (SEMANTIC): Sugestão de estilo: considere encerrar o bloco condicional IF com 'END-IF' explícito
    11 | 001100     IF VALOR-1
       |            ^
--------------------------------------------------------------------------
Resumo de Diagnósticos (test.cob): 0 Severe (S), 0 Error (E), 0 Warning (W), 1 Informational (I).
[COBython] 4/4 Gerando código com backend 'llvm' (otimização -O3) -> 'test_o3'...
  $ /usr/bin/clang -O3 /tmp/cobython_8dia79sg.ll -lm -o test_o3
[COBython] Saída (executable, -O3) gerada via clang em: 'test_o3'
ESTA STRING FOI                                 QUEBRADA EM DUAS LINHAS.
```

### 2. Emissão de LLVM IR Otimizado (`-S -emit-llvm -O2`), Assembly (`.s`) e Objeto (`.o`)
```bash
python3 cobython.py test.cob -S -emit-llvm -O2 -o test_opt.ll
python3 cobython.py test.cob -S -O2 -o test.s
python3 cobython.py test.cob -c -O2 -o test.o
```

### 3. Notificação dos 4 Níveis de Severidade da Seção 6 (`I`, `W`, `E`, `S`)
Exemplo de saída de diagnósticos com trecho do código-fonte e ponteiro `^`:
```text
diag_iw.cob:5:12: [W - WARNING] (PARSER): Nível 77 deveria iniciar na Área A (colunas 8-11)
     5 |            77 VAR-GRANDE PIC X(10) VALUE "ABCDEFGHIJ".
       |            ^
diag_iw.cob:10:12: [W - WARNING] (SEMANTIC): Potencial truncamento em MOVE: variável de origem 'VAR-GRANDE' (10 bytes) é maior que o destino 'VAR-CURTA' (3 bytes)
    10 |            MOVE VAR-GRANDE TO VAR-CURTA.
       |            ^
diag_iw.cob:11:12: [I - INFORMATIONAL] (SEMANTIC): Sugestão de estilo: considere encerrar o bloco condicional IF com 'END-IF' explícito
    11 |            IF VAR-CURTA = "ABC"
       |            ^
diag_iw.cob:7:8: [I - INFORMATIONAL] (SEMANTIC): Sugestão de otimização: variável 'VAR-NAO-USADA' declarada na WORKING-STORAGE SECTION não é referenciada
     7 |        77 VAR-NAO-USADA PIC 9(2).
       |        ^
--------------------------------------------------------------------------
Resumo de Diagnósticos (diag_iw.cob): 0 Severe (S), 0 Error (E), 2 Warning (W), 2 Informational (I).
```
- Com `-Werror`, os avisos `W` são promovidos para `E` e bloqueiam a geração de código (código de saída `1`).
- Com `-Wno-info` ou `-w`, as mensagens informativas/avisos são filtradas conforme esperado.

### 4. Acoplamento de Backends Customizados (`--backend`)
- Testado o carregamento dinâmico de um transpilador externo (`--backend py_transpiler.py`), confirmando que usuários podem adicionar transpiladores para C/Python ou geradores de assembly direto herdando de `CodegenBackend` ou definindo `generate(program, options)`.
