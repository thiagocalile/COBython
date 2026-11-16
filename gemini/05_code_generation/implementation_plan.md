# Plano de Implementação: Geração de Código Intermediário LLVM IR (`llvm_codegen.py` — Parte 5)

## 1. Avaliação Comparativa das Três Abordagens (Seção 5)

A **Seção 5 (Geração de Código Intermediário / Tradução)** da [ESPECIFICAÇÃO TEXTUAL.txt](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/ESPECIFICAÇÃO%20TEXTUAL.txt) prevê a tradução da AST para uma representação intermediária ou código objeto. Abaixo está a análise técnica das três opções avaliadas:

| Critério | 1. Transpilação para Linguagem Alvo (C / Python) | 2. Código-Objeto / Bytecode Direto (JVM `.class`) | 3. Representação Intermediária LLVM (`.ll`) — **Escolhida** |
| :--- | :--- | :--- | :--- |
| **Fidelidade ao modelo de memória COBOL (`01` / `02-49` / `OCCURS`)** | **Alta (em C)**: structs/buffers de `char` contíguos permitem `memcpy` de grupos inteiros. | **Baixa**: a JVM não possui aritmética de ponteiros sobre fatias de um array de bytes para subcampos. | **Altíssima**: globais `[N x i8]` + instrução `getelementptr` (GEP) modelam exatamente os deslocamentos (*offsets*) e *strides* de registros e tabelas `OCCURS`. |
| **Didática e Arquitetura de Compiladores** | **Média**: delega a geração de código de três endereços e controle de fluxo de baixo nível ao compilador alvo. | **Média/Baixa**: exige geração binária de *Constant Pool* e *StackMapTable* sem ganhos na análise de otimização. | **Excelente**: utiliza forma SSA (*Static Single Assignment*), blocos básicos explícitos (`br i1`, `icmp`, `fcmp`, `call`) e tipos estáticos. |
| **Reaproveitamento de Infraestrutura** | Usa `gcc` ou interpretador Python. | Exige `java` e montador externo (ex.: Jasmin) para texto. | Usa toda a suíte LLVM (`clang`, `opt`, `llc`, `lli`) e vincula diretamente com a `libc` padrão (`printf`, `fopen`, `memcpy`, `pow`). |
| **Manutenibilidade em Python puro** | Simples (emissão de strings C/Python). | Complexa sem bibliotecas externas. | Limpa e modular: o arquivo `.ll` é 100% textual, legível e não exige bibliotecas nativas em Python para ser emitido. |

> [!IMPORTANT]
> **Nota sobre o ambiente local**: No sistema atual, o `gcc` está instalado, mas os executáveis `clang` / `llc` / `lli` ainda não estão presentes no `PATH`. O `llvm_codegen.py` gerará sempre o arquivo `.ll` válido e completo, e verificará automaticamente via `shutil.which` se `clang` ou `llc`+`gcc` estão disponíveis para compilar o binário executivo quando solicitado.

---

## 2. Arquitetura do `llvm_codegen.py`

```mermaid
flowchart LR
    A["Arquivo Fonte (.cob)"] --> B["lexer.py"]
    B --> C["parser.py"]
    C --> D["semantic_analyzer.py"]
    D --> E["llvm_codegen.py\ngenerate_llvm_ir()"]
    E --> F["Arquivo LLVM IR (.ll)"]
    F -. "Se clang / llc disponível" .-> G["Executável Nativo"]
```

---

## 3. Alterações Propostas

### Gerador de Código LLVM IR

#### [NEW] [llvm_codegen.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/llvm_codegen.py)

1. **Pipeline e Interface Pública**:
   - Integrará [lexer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/lexer.py), [parser.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/parser.py) e [semantic_analyzer.py](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/semantic_analyzer.py): se houver erros léxicos, sintáticos ou semânticos (`E` ou `S`), interrompe a compilação antes da geração de código e exibe a tabela de diagnósticos.
   - Exporá a função `generate_llvm_ir(program: ProgramNode) -> str`, que retorna o módulo LLVM IR completo em texto.
   - Na CLI:
     ```bash
     python3 llvm_codegen.py <arquivo_fonte.cob> [-o saida.ll] [--compile] [--debug]
     ```
     - Por padrão, gera `<nome_base>.ll` no mesmo diretório (ou no caminho passado em `-o`).
     - Com `--compile` (ou automaticamente se encontrar `clang`/`llc`), invoca o backend nativo para produzir o binário executável.

2. **Modelo de Memória e Layout da `DATA DIVISION` (`DataLayoutBuilder`)**:
   - **Cálculo de Tamanho e Offset (`FieldLayout`)**:
     - Analisa cada máscara `PIC` (ex.: `9(5)V99` → 7 bytes, 2 casas decimais; `X(30)` → 30 bytes; `-ZZZZZZZZZ9.99` → 14 bytes editado).
     - Todo item raiz (`01` ou `77`) é emitido como um buffer global de bytes no LLVM IR:
       ```llvm
       @var_TAB_TRABALHADOR = internal global [48 x i8] c"                                                "
       ```
     - Subcampos (`02` a `49`) não alocam memória separada: eles referenciam o buffer global do seu registro `01` raiz através do seu `base_offset` em bytes!
     - Tabelas com `OCCURS N TIMES` (1D ou 2D, como `TRABALHADOR(IDX)` e `DPTO(IDX, 2)` em `prog004` e `prog006`) armazenam o *stride* (tamanho em bytes de cada elemento da dimensão). O endereço em tempo de execução é calculado com aritmética SSA (`sub`, `mul`, `add`) + `getelementptr inbounds i8, ptr @var_ROOT, i64 %offset`.
     - Dessa forma, um `MOVE` para um item de grupo (ex.: `MOVE "JOSEA1111111..." TO TAB-TRABALHADOR`) preenche imediatamente todos os subcampos e ocorrências subordinadas, reproduzindo fielmente a semântica do COBOL.
   - **Índices (`INDEXED BY`) e Descritores de Arquivo (`FD`)**:
     - Índices implícitos (como `TID`) são emitidos como `@idx_TID = internal global i64 1`.
     - Cada arquivo `FD` recebe um ponteiro global `@file_FD_NOME = internal global ptr null` (`FILE*` da `libc`) e uma variável de status se declarada em `FILE STATUS`.

3. **Biblioteca de Runtime Embutida no Módulo `.ll`**:
   - Declarações externas da `libc` e intrínsecos LLVM:
     - `@printf`, `@snprintf`, `@scanf`, `@fgets`, `@putchar`, `@puts`, `@exit`
     - `@fopen`, `@fclose`, `@fread`, `@fwrite`, `@fseek`
     - `@strlen`, `@strncmp`, `@strtod`, `@toupper`
     - `@llvm.memcpy.p0.p0.i64`, `@llvm.memset.p0.i64`, `@llvm.pow.f64`
   - Funções auxiliares internas geradas no próprio `.ll`:
     - `@__cobol_read_num(ptr %buf, i64 %len, i64 %scale) -> double`: converte um campo numérico ASCII/zonado do buffer COBOL para `double`.
     - `@__cobol_write_num(double %val, ptr %buf, i64 %len, i64 %scale, i32 %is_edited) -> void`: formata um valor numérico de volta para o buffer COBOL (suportando casas decimais implícitas `V` e máscaras de edição como `-ZZZZZZZZZ9.99`).
     - `@__cobol_move_alpha(ptr %dst, i64 %dst_len, ptr %src, i64 %src_len) -> void`: copia bytes e preenche o restante com espaços (`0x20`).
     - `@__cobol_cmp_alpha(ptr %a, i64 %alen, ptr %b, i64 %blen) -> i32`: compara strings/buffers respeitando o preenchimento com espaços do COBOL.
     - Helpers para `INSPECT` (`TALLYING`, `REPLACING`, `CONVERTING`).

4. **Tradução das Instruções da `PROCEDURE DIVISION` (`LLVMIRGenerator`)**:
   - **Parágrafos e Seções**:
     - Cada parágrafo/seção é emitido como uma função interna `define internal void @para_<NOME>()` e a função `@main() -> i32` executa o fluxo principal da `PROCEDURE DIVISION`.
   - **Comandos**:
     - `DISPLAY`: emite chamadas a `@printf` (`"%.*s"` para literais/buffers alfanuméricos e formatação para numéricos) e `@putchar(10)` (omitido se `WITH NO ADVANCING`).
     - `ACCEPT`: lê da entrada padrão (`stdin`) e grava no buffer ou variável numérica de destino.
     - `MOVE`: suporta literais, constantes figurativas (`ZEROS`, `SPACES`, `LOW-VALUES`), `FUNCTION UPPER-CASE` e variáveis escalares/grupos/indexadas.
     - `ADD`, `SUBTRACT`, `MULTIPLY`, `DIVIDE` (com `GIVING` e `REMAINDER`), `COMPUTE` (com `+`, `-`, `*`, `/`, `**` via `@llvm.pow.f64`).
     - `IF / ELSE / END-IF`: avalia expressões relacionais e lógicas (`AND`, `OR`, `NOT`, condições abreviadas e condições simples) gerando `fcmp`/`icmp` e desvios condicionais `br i1 %cond, label %then, label %else`.
     - `PERFORM`: emite chamadas `call void @para_<NOME>()` e laços `br label %loop.cond` para `PERFORM ... UNTIL <cond>` ou `TIMES`.
     - `GO TO`: emite chamada ao parágrafo alvo seguida de encerramento do fluxo atual.
     - `EVALUATE`: emite cadeia de blocos `when_check` / `when_body` / `eval_end`.
     - `SEARCH`: emite laço sobre o índice da tabela (`1` até `OCCURS`), avaliando as cláusulas `WHEN` e o bloco `AT END`.
     - `SET`: atualiza variáveis de índice (`TO`, `UP BY`, `DOWN BY`).
     - `INSPECT`: invoca as rotinas auxiliares de `TALLYING`, `REPLACING` e `CONVERTING`.
     - `OPEN`, `READ`, `WRITE`, `CLOSE`, `START`: invoca `@fopen`, `@fread`, `@fwrite`, `@fclose` sobre o `FILE*` global e atualiza a variável de `FILE STATUS` (quando associada).
     - `STOP RUN`: chama `call void @exit(i32 0)`.

---

## 4. Plano de Verificação

### Testes Automatizados
1. **Geração de `.ll` para [test.cob](file:///home/coscal/JD/10-19_Projetos/10_Projetos_Correntes/COBython/test.cob)**:
   ```bash
   python3 llvm_codegen.py test.cob
   ```
   Verificando que `test.ll` é criado sem erros e contém as definições globais (`@var_REGISTRO`, `@var_VALOR_1`), as funções de parágrafo/seção (`@sec_INICIO`, `@main`) e todas as instruções SSA bem formadas.
2. **Geração de `.ll` para todos os 7 programas da suíte (`prog001` a `prog007`)**:
   - Reconstruiremos/executaremos sobre os 7 programas válidos (`prog001_divisoes` a `prog007_loja`) garantindo que todos gerem arquivos `.ll` completos e sintaticamente válidos em LLVM IR.
3. **Bloqueio de geração em programas com erro**:
   - Executaremos `llvm_codegen.py` sobre `resultados_lexer/erros/*.cob` para confirmar que o gerador recusa a emissão de código quando há erros léxicos, sintáticos ou semânticos (`E`/`S`), retornando código de saída `1`.

### Verificação Manual
- O usuário poderá inspecionar os arquivos `.ll` gerados (ex.: `test.ll`) e, caso instale `clang` ou `llvm` (`sudo apt install clang`), compilá-los e executá-los diretamente com `clang test.ll -lm -o test && ./test`.
