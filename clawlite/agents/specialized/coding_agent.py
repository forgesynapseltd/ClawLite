"""
ClawLite - Local-First Personal AI Assistant
Copyright (c) 2026 FORGESYNAPSE LTD (Company No. 16692140). All Rights Reserved.
Registered Office: Unit A, 82 James Carter Road, Mildenhall, IP28 7DE, UK.
Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE for full terms.
SPDX-License-Identifier: Apache-2.0

agents/specialized/coding_agent.py — Agente que desarrolla proyectos de código
Trabaja en un sandbox Docker aislado con red habilitada.
Itera: planifica → escribe → ejecuta → corrige → entrega.
"""

import ast
import builtins
import json
import re
import sys
from dataclasses import dataclass, field
from loguru import logger
from clawlite.llm.client import llm
from clawlite.sandbox.agent_sandbox import AgentSandbox
from clawlite.llm.json_parser import extract_json
from clawlite.personality.language import get_target_language
from clawlite.agent.tools.brief import _lang_directive


PLAN_PROMPT = """You are a senior software engineer. The user wants you to build something.

User request:
{request}

Generate a step-by-step plan. Return ONLY a JSON:
{{
  "project_name": "short-name-with-hyphens",
  "description": "what this project does (1-2 sentences)",
  "language": "python|node|other",
  "dependencies": ["package1", "package2"],
  "files": [
    {{"path": "main.py", "purpose": "entry point"}},
    {{"path": "utils.py", "purpose": "helper functions"}}
  ],
  "test_files": [
    {{"path": "test_utils.py", "purpose": "tests for utils.py functions"}}
  ],
  "testable": true,
  "testable_reason": "explain why if testable=false (e.g. 'requires live API with no mockable interface')",
  "run_command": "python main.py",
  "test_command": "pytest -v"
}}

Rules:
- Keep it simple — typically 2-4 source files
- Only include packages that exist on PyPI/npm
- run_command must work from project root

TESTING RULES (critical):
- For Python projects, ALWAYS include pytest in `dependencies` when testable=true
- For Node projects, include the test framework (jest, mocha, etc.) in `dependencies`
- Tests should be deterministic: mock external APIs, network calls, filesystem when needed
- Tests verify the FUNCTIONS in `files`, not the run_command behavior
- Each significant function should have at least one test
- If the task is fundamentally untestable in isolation (e.g. "fetch live Bitcoin price and print"
  where the value is unpredictable), set testable=false and explain in testable_reason.
  In that case, you can omit test_files and test_command.
- Prefer testable design: if the request can be structured so that core logic is testable
  (e.g. separate API fetching from min/max calculation), do so and test the pure logic.
"""

CODE_PROMPT = """You are writing code for a project.

Project: {project_name}
Description: {description}
File to write: {file_path}
Purpose: {purpose}

Available third-party packages (this is the COMPLETE list -- nothing else is installed):
{dependencies}

Full project context:
{context}

Write the COMPLETE content of this file. No placeholders, no TODOs.
Only import Python's standard library or a package from the list above. Do NOT import any other
third-party package, no matter how standard or common it seems (e.g. a mocking helper, a testing
utility, a date/time library) -- if you need to mock something and no mocking library is listed
above, use Python's built-in `unittest.mock` module directly (patch, MagicMock), never the pytest
`mocker` fixture (that requires an undeclared package).
Return ONLY the file content. No markdown code fences. No explanations."""

FIX_DECIDE_PROMPT = """You are a senior engineer fixing a project where tests are failing.

=== ALL PROJECT FILES (source code AND test files) ===
{all_files}
=== END PROJECT FILES ===

=== TEST FAILURES (the objective truth — these are what must pass) ===
{test_failures}
=== END TEST FAILURES ===

{additional_context}

Your job: identify ONE file to modify so the failing tests pass.

CRITICAL RULES:
1. The tests define the contract. The fix must make them pass without weakening them.
2. Priority order when deciding what to fix:
   a) First consider: is a source file (in `files`) broken? → fix the source file
   b) Only if the source code is clearly correct AND a test contains an obvious mistake
      (wrong expected value, broken mock, wrong import path), fix the TEST file
3. NEVER weaken or delete tests just to make them pass. If a test is wrong, fix it correctly
   (e.g. correct an expected value), don't disable it or change assertion to `True`.
4. The file you choose must be a real path from the project files above.

Respond with ONLY this JSON (no prose, no markdown fences):
{{
  "file_to_fix": "exact path from the project files",
  "is_test_file": true or false,
  "diagnosis": "one sentence: why this file causes the test failure",
  "what_to_change": "one sentence: the concrete change needed"
}}"""


FIX_DECIDE_RECONSIDER_PROMPT = """Your previous diagnosis was:
File: {previous_file}
Reason: {previous_diagnosis}

But pytest's own traceback shows the actual exception was raised in a DIFFERENT
file: {failing_files}. Your previous choice does not match any of these files --
reconsider which file actually needs to change, using the traceback as the
objective source of truth.

=== ALL PROJECT FILES (source code AND test files) ===
{all_files}
=== END PROJECT FILES ===

=== TEST FAILURES (the objective truth -- these are what must pass) ===
{test_failures}
=== END TEST FAILURES ===

Respond with ONLY this JSON (no prose, no markdown fences):
{{
  "file_to_fix": "exact path from the project files",
  "is_test_file": true or false,
  "diagnosis": "one sentence: why this file causes the test failure",
  "what_to_change": "one sentence: the concrete change needed"
}}"""


FIX_REWRITE_PROMPT = """Rewrite the file {file_to_fix} so the failing tests pass.

This file is currently:
=== CURRENT {file_to_fix} ===
{current_content}
=== END ===

Other files in the project (context — DO NOT output these, only {file_to_fix}):
{other_files}

=== TEST FAILURES ===
{test_failures}
=== END ===

Diagnosis: {diagnosis}
Required change: {what_to_change}

Output the COMPLETE corrected content of {file_to_fix} and nothing else.
- Plain code only. No JSON. No markdown code fences. No explanations.
- Include ALL imports and all code that should remain in the file.
- If this is a test file, NEVER weaken assertions to force a pass — fix them correctly.
- If this is a source file, make the actual behavior match what the tests expect.
- If this is a test file that mocks a dependency, make sure the function under
  test actually receives/uses the mocked value (never pass a hardcoded literal
  like None disconnected from the mock) -- the whole point of the mock is that
  its return value flows into the call being tested.
- Start directly with the first line of code."""

VALIDATE_PROMPT = """You are a senior code reviewer. Decide if a program correctly fulfills a user request.

You will see: (1) the original user request, (2) all source files of the program,
(3) the actual output the program produced when executed.

Your task: judge whether THIS code, with THIS output, genuinely satisfies what the user asked for.

User request:
{request}

Source files:
{all_files}

Program output (what the user would see):
{output}

Reason about three things:
1. Does the code actually implement what the user requested, or does it only pretend to?
   (e.g. a function that returns None instead of computing the real value pretends to work)
2. Is the output a real, useful result for the user's request? Would a reasonable person
   reading this output feel their request was answered?
3. If you re-read the code, does the output match what the code SHOULD produce, or does
   it reveal a logic bug, incorrect data parsing, missing computation, or a silent failure?

Return ONLY a JSON:
{{
  "satisfies": true or false,
  "issue": "specific problem found, in one sentence (empty if satisfies=true)",
  "fix_hint": "concrete instruction for what to change in the code (empty if satisfies=true)"
}}

Be honest. If the output is technically present but the user would not consider their
request fulfilled, mark satisfies=false. The user came for a real answer, not a placeholder."""


# Mapeo PyPI -> nombre real de import, para los casos donde difieren --
# necesario para que la validación de paquetes permitidos no genere falsos positivos.
_PACKAGE_TO_IMPORT_NAME = {
    "beautifulsoup4": "bs4",
    "pyyaml": "yaml",
    "pillow": "pil",
    "python-dateutil": "dateutil",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
}

# Fixtures de pytest conocidos que requieren un paquete no siempre obvio
# (mocker requiere pytest-mock instalado, no viene con pytest base).
_KNOWN_UNDECLARED_FIXTURES = {
    "mocker": "pytest-mock",
}


@dataclass
class ContentViolations:
    """
    Resultado estructurado de _validate_content -- separa las clases de
    violación (en vez de texto ya formateado) para poder extender el sistema
    con nuevas validaciones AST sin tener que reparsear mensajes.
    """
    syntax_error: dict | None = None  # {"msg": str, "lineno": int, "offset": int}
    undeclared_dependencies: list = field(default_factory=list)
    undefined_names: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.syntax_error or self.undeclared_dependencies or self.undefined_names)

    def as_text(self) -> str:
        parts = []
        if self.syntax_error:
            se = self.syntax_error
            parts.append(
                f"syntax error at line {se.get('lineno')}, column {se.get('offset')}: {se.get('msg')}"
            )
        if self.undeclared_dependencies:
            parts.append(f"undeclared import(s)/package(s): {', '.join(self.undeclared_dependencies)}")
        if self.undefined_names:
            parts.append(f"name(s) used but never imported/defined: {', '.join(self.undefined_names)}")
        return "; ".join(parts)


FIX_CONTENT_VIOLATIONS_PROMPT = """The following file has one or more problems:

=== CURRENT {file_path} ===
{current_content}
=== END ===

Problems found: {violations}

Rewrite the COMPLETE file fixing all of the problems listed above:
- If a problem mentions a syntax error, make sure the output is valid, complete Python
  code -- check for stray text, incomplete statements, or leftover formatting artifacts
  (e.g. a line like "=== CURRENT file.py ===") that don't belong in real code.
- If a problem mentions a package/import that is NOT in the available packages list
  below, remove it and use only Python's standard library or a package from that list
  (use `unittest.mock` directly -- Mock, MagicMock, patch -- never the pytest `mocker`
  fixture, never any other undeclared package).
- If a problem mentions a name that is used but never imported or defined, add the
  missing import or definition (or fix the typo if it's clearly a typo of something
  already imported/defined).

Available third-party packages (the ONLY ones installed):
{dependencies}

Output ONLY the corrected file content. No markdown code fences. No explanations."""


class CodingAgent:
    """
    Agente que desarrolla proyectos completos en un sandbox aislado.
    Notifica el progreso al usuario en cada paso clave.
    """

    # Base mínima de intentos de fix. Se escala adaptativamente en _run_with_tests
    # según la cantidad de tests del proyecto (más tests = más superficie a estabilizar).
    MIN_FIX_ATTEMPTS = 3
    MAX_FIX_ATTEMPTS = 8
    # Compat: alias para flujos legacy que aún referencian MAX_FIX_ATTEMPTS como límite fijo

    def __init__(self, progress_callback=None):
        """
        progress_callback: función async(text) que envía actualizaciones al usuario.
        Permite que el usuario vea el progreso en tiempo real.
        """
        self.progress_callback = progress_callback

    async def _notify(self, message: str):
        if self.progress_callback:
            try:
                await self.progress_callback(message)
            except Exception as e:
                logger.debug(f"Progress callback failed: {e}")

    async def run(self, user_id: str, request: str) -> dict:
        logger.info(f"💻 CodingAgent: {request[:80]}")

        # Paso 1: Plan
        await self._notify("💡")
        plan = await self._create_plan(request)
        if not plan:
            return {
                "agent": "coding",
                "success": False,
                "summary": "❌ Could not generate a plan for this request.",
                "files": [],
            }

        testable = bool(plan.get("testable", False))
        test_files = plan.get("test_files", []) or []
        n_tests = len(test_files) if testable else 0

        plan_msg = (
            f"📋 *{plan['project_name']}*\n"
            f"{plan['description']}\n\n"
            f"📄 {len(plan['files'])}"
            + (f" + 🧪 {n_tests}" if n_tests else "")
            + f"\n💻 {plan['language']}"
        )
        if not testable and plan.get("testable_reason"):
            plan_msg += f"\n⚠️ {plan['testable_reason']}"
        await self._notify(plan_msg)

        # Paso 2: Sandbox. Red SOLO si el plan declara dependencias reales (más
        # allá de pytest, preinstalado) -- antes era networked=True siempre,
        # sin importar la tarea. pytest se filtra ANTES de decidir la red
        # porque el prompt del plan lo agrega SIEMPRE que testable=true
        # (la mayoría de los casos) -- sin este filtro, casi todo proyecto
        # parecería "necesitar red" aunque fuera lógica pura sin ningún
        # acceso externo (falso positivo confirmado empíricamente contra
        # el LLM real antes de proponer este cambio).
        # Límite aceptado: si el código generado hace una llamada de red en
        # runtime sin declarar NINGÚN paquete nuevo, perdería la red -- el
        # fallo sería visible en el log de ejecución, no silencioso. Mejora
        # futura sugerida por el auditor: que el plan declare explícitamente
        # network_required en vez de inferirlo de dependencies (conceptos
        # distintos, hoy correlacionados pero no equivalentes) -- expediente
        # aparte, no bloqueante para este cambio.
        deps = [
            d for d in (plan.get("dependencies") or [])
            if not d.lower().startswith("pytest")
        ]
        with AgentSandbox(networked=bool(deps)) as sandbox:
            if not sandbox._started:
                return {
                    "agent": "coding",
                    "success": False,
                    "summary": "No pude iniciar el entorno de desarrollo.",
                    "files": [],
                }

            if deps:
                await self._notify(f"📦 {', '.join(deps)}")
                deps_str = " ".join(deps)
                if plan["language"] == "python":
                    install = sandbox.exec(f"pip install --no-cache-dir {deps_str}", timeout=180)
                elif plan["language"] == "node":
                    install = sandbox.exec(f"npm install {deps_str}", timeout=180)
                else:
                    install = None

                if install and not install.success:
                    await self._notify("⚠️")

            # Paso 4: Escribir archivos de código.
            # Acumulamos el código REAL ya generado para que los tests (paso 4b) se
            # escriban contra la interfaz real, no contra una idea abstracta del plan.
            # Esto evita que tests y código diverjan (test espera función, código
            # implementa método, etc.) — la causa raíz de tests que nunca pasan.
            files_written = []
            written_code = {}  # path -> contenido real, contexto para los tests
            for file_spec in plan["files"]:
                await self._notify(f"✍️ {file_spec['path']}")
                content = await self._generate_validated_file_content(plan, file_spec)
                if sandbox.write_file(file_spec["path"], content):
                    files_written.append(file_spec["path"])
                    written_code[file_spec["path"]] = content

            # Paso 4b: Escribir archivos de test (solo si testable).
            # Les pasamos el código fuente real ya escrito para que los tests
            # verifiquen lo que EXISTE, no lo que imaginaron.
            if testable:
                for test_spec in test_files:
                    await self._notify(f"🧪 {test_spec['path']}")
                    content = await self._generate_validated_file_content(
                        plan, test_spec, source_code=written_code
                    )
                    if sandbox.write_file(test_spec["path"], content):
                        files_written.append(test_spec["path"])

            if not files_written:
                return {
                    "agent": "coding",
                    "success": False,
                    "summary": "❌ Could not create the project files.",
                    "files": [],
                }

            # Paso 5: Iterar — tests como árbitro principal si testable, sino flujo semántico
            run_cmd = plan.get("run_command", "")
            test_cmd = plan.get("test_command", "") if testable else ""
            execution_log = ""
            verdict = "unknown"

            if testable and test_cmd:
                verdict, execution_log = await self._run_with_tests(
                    sandbox, plan, test_cmd, run_cmd
                )
            elif run_cmd:
                verdict, execution_log = await self._run_with_semantic_validation(
                    sandbox, plan, request, run_cmd
                )

            # Paso 6: Recoger archivos finales
            final_files = {}
            for file_path in sandbox.list_files():
                content = sandbox.read_file(file_path)
                if content is not None:
                    final_files[file_path] = content

            # Éxito HONESTO: solo si el veredicto confirma que de verdad funciona.
            # Nunca por defecto — un proyecto con tests rotos NO es un éxito.
            succeeded = verdict in self._SUCCESS_VERDICTS

            return {
                "agent": "coding",
                "success": succeeded,
                "project_name": plan["project_name"],
                "description": plan["description"],
                "language": plan["language"],
                "files": final_files,
                "run_command": run_cmd,
                "test_command": test_cmd,
                "verdict": verdict,
                "execution_log": execution_log[:3000],
                "summary": await self._build_summary(plan, final_files, verdict),
            }

    # ───────────────────────────────────────────────────────────────────
    # FLUJO A: TESTS como árbitro objetivo (preferido)
    # ───────────────────────────────────────────────────────────────────

    def _count_collected_tests(self, output: str) -> int:
        """
        Extrae el número de tests recolectados por pytest del output.
        Pytest siempre imprime 'collected N items' al inicio.
        Si no se puede parsear (pytest petó antes de recolectar), retorna 0.
        """
        m = re.search(r"collected (\d+) items?", output)
        return int(m.group(1)) if m else 0

    def _scale_attempts_from_test_count(self, n_tests: int) -> int:
        """
        Escala los intentos de fix según la superficie real de tests del proyecto.
        Base MIN_FIX_ATTEMPTS, +1 por cada 5 tests, tope MAX_FIX_ATTEMPTS.
        """
        scaled = self.MIN_FIX_ATTEMPTS + (n_tests // 5)
        return min(scaled, self.MAX_FIX_ATTEMPTS)

    async def _run_with_tests(
        self,
        sandbox: AgentSandbox,
        plan: dict,
        test_cmd: str,
        run_cmd: str,
    ) -> tuple[str, str]:
        """
        Ejecuta tests y corrige iterativamente hasta que pasen.
        Cuando pasen, ejecuta run_cmd como sanity check final.
        Retorna (verdict, execution_log).
        """
        # Empezamos con el mínimo. El escalado se recalcula DENTRO del loop:
        # cuando pytest logra recolectar tests (puede ser tras varios fixes si
        # los archivos iniciales tenían errores de sintaxis), max_attempts sube
        # en caliente. Esto evita perder la oportunidad de escalar cuando la
        # primera ejecución colapsa por collection error.
        max_attempts = self.MIN_FIX_ATTEMPTS

        await self._notify(f"🧪 `{test_cmd}`")
        test_result = sandbox.exec(test_cmd, timeout=90)
        execution_log = test_result.output

        attempt = 0
        # Freno de convergencia: si los fallos no bajan en intentos consecutivos,
        # el fixer no está progresando. Seguir solo quema API sin acercarse a la
        # solución. Cortamos tras STALL_LIMIT intentos sin mejora.
        STALL_LIMIT = 2
        prev_failed = None
        stalled = 0
        while attempt < max_attempts:
            if test_result.success:
                break

            # Recalcular escalado con cada ejecución de pytest.
            # Solo escala hacia arriba: nunca reducimos el límite ya alcanzado.
            n_collected = self._count_collected_tests(test_result.output)
            if n_collected > 0:
                scaled = self._scale_attempts_from_test_count(n_collected)
                if scaled > max_attempts:
                    logger.info(
                        f"🎯 Tests recolectados: {n_collected} → max_attempts "
                        f"escalado de {max_attempts} a {scaled}"
                    )
                    max_attempts = scaled

            attempt += 1
            n_failed = self._count_failed_tests(test_result.output)
            n_collected_now = self._count_collected_tests(test_result.output)

            # Estancamiento: el fixer no progresa si, en intentos consecutivos, ni baja
            # el nº de fallos ni consigue siquiera recolectar tests (p.ej. imports que no
            # cuadran y se "arreglan" en círculos — el caso real que vimos). Cortar evita
            # iteraciones inútiles y entrega con un veredicto HONESTO en vez de mentir.
            if n_collected_now == 0:
                stalled += 1          # sigue sin poder importar/recolectar: no-progreso
            elif n_failed > 0 and prev_failed is not None and n_failed >= prev_failed:
                stalled += 1          # los fallos no bajan: no-progreso
            else:
                stalled = 0           # hubo avance (recolectó tests o bajaron los fallos)
            prev_failed = n_failed if n_collected_now > 0 else None

            if stalled >= STALL_LIMIT:
                logger.warning(
                    f"⛔ Estancado: el fixer no progresa ({stalled} intentos sin avance)"
                )
                await self._notify("⚠️")
                break

            if n_collected_now == 0 or n_failed == 0:
                # Sin tests recolectados, o fallo sin 'failed' contables (errores de
                # import/colección donde pytest reporta 'errors', no 'failed'):
                # mostrar "❌ 0" sería contradictorio. El taller (🔧) cubre ambos.
                await self._notify(f"🔧 {attempt}/{max_attempts}")
            else:
                await self._notify(f"❌ {n_failed} · {attempt}/{max_attempts}")

            fixed = await self._fix_error(
                sandbox, plan,
                error_result=test_result,
                test_failures=self._extract_test_failures(test_result.output),
            )
            if not fixed:
                break

            test_result = sandbox.exec(test_cmd, timeout=90)
            execution_log = test_result.output

        if test_result.success:
            await self._notify("✅")
            # Sanity check: ejecutar run_command para confirmar que el programa corre
            if run_cmd:
                await self._notify(f"🚀 `{run_cmd}`")
                run_result = sandbox.exec(run_cmd, timeout=60)
                execution_log = (
                    f"=== TESTS ===\n{test_result.output[:1500]}\n\n"
                    f"=== RUN ===\n{run_result.output[:1500]}"
                )
                if run_result.success:
                    return "tests_pass_and_runs", execution_log
                # Los TESTS son el árbitro objetivo: si pasan, la lógica está verificada.
                # El sanity-check en seco es señal SECUNDARIA y NUNCA degrada un proyecto
                # con tests verdes a "roto" — eso sería un falso negativo (p.ej. un programa
                # interactivo da EOFError sin teclado). Se reporta el aviso con transparencia
                # (el log va al usuario) sin adivinar por coincidencia de texto en la salida.
                return "tests_pass_run_warn", execution_log
            return "tests_pass", execution_log

        return "tests_failing", execution_log

    # ───────────────────────────────────────────────────────────────────
    # FLUJO B: validación semántica (cuando no hay tests viables)
    # ───────────────────────────────────────────────────────────────────

    async def _run_with_semantic_validation(
        self,
        sandbox: AgentSandbox,
        plan: dict,
        request: str,
        run_cmd: str,
    ) -> tuple[str, str]:
        """
        Flujo legacy para proyectos no testeables: ejecuta run_cmd y valida
        semánticamente el output con el LLM.
        """
        await self._notify(f"🚀 `{run_cmd}`")
        result = sandbox.exec(run_cmd, timeout=60)
        execution_log = result.output

        for attempt in range(1, self.MIN_FIX_ATTEMPTS + 1):
            if not result.success:
                await self._notify(f"🔧 {attempt}/{self.MIN_FIX_ATTEMPTS}")
                fixed = await self._fix_error(sandbox, plan, result)
                if not fixed:
                    break
            else:
                await self._notify("🔍")
                validation = await self._validate_output(
                    request, execution_log, sandbox, plan
                )

                if validation.get("satisfies", False):
                    await self._notify("✅")
                    return "semantic_pass", execution_log

                issue = validation.get("issue", "")
                await self._notify(f"🔧 {issue}\n{attempt}/{self.MIN_FIX_ATTEMPTS}")
                fixed = await self._fix_error(
                    sandbox, plan, result,
                    semantic_hint=validation.get("fix_hint", ""),
                )
                if not fixed:
                    break

            result = sandbox.exec(run_cmd, timeout=60)
            execution_log = result.output

        return "semantic_unverified", execution_log

    # ───────────────────────────────────────────────────────────────────
    # PARSING de output de tests
    # ───────────────────────────────────────────────────────────────────

    def _count_failed_tests(self, output: str) -> int:
        """Cuenta tests fallidos en la salida de pytest."""
        # pytest summary: "1 failed, 3 passed" o "FAILED test_x.py::test_y"
        m = re.search(r"(\d+)\s+failed", output)
        if m:
            return int(m.group(1))
        return output.count("FAILED")

    def _extract_test_failures(self, output: str) -> str:
        """
        Extrae las secciones relevantes de fallos de pytest:
        - Cabecera FAILED con nombres
        - Tracebacks de cada fallo
        - Summary final
        Limita a 3000 chars para no inflar el prompt.
        """
        # pytest separa fallos con líneas de '_' o '='
        # Buscamos las secciones que comienzan con "FAILED" o "_____ test_xxx _____"
        lines = output.split("\n")

        # Estrategia simple y robusta: tomar todo lo que esté entre el primer FAILED/ERROR
        # y el resumen final. Si no encontramos marcadores, devolvemos los últimos 2500 chars.
        start = None
        for i, line in enumerate(lines):
            if "FAILED" in line or "ERROR" in line or line.startswith("____"):
                start = i
                break

        if start is None:
            return output[-2500:] if output else "(no output)"

        chunk = "\n".join(lines[start:])
        return chunk[:3000] if len(chunk) > 3000 else chunk

    # ───────────────────────────────────────────────────────────────────
    # VALIDACIÓN ESTÁTICA (AST) de dependencias no declaradas
    # ───────────────────────────────────────────────────────────────────

    def _extract_imports(self, code: str) -> set:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return set()
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.add(node.module.split(".")[0])
        return modules

    def _extract_test_fixture_names(self, code: str) -> set:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return set()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                for arg in node.args.args:
                    names.add(arg.arg)
        return names

    def _build_allowed_packages(self, dependencies: list, project_files: list | None = None) -> set:
        allowed = set()
        for dep in dependencies:
            dep_lower = dep.lower().strip()
            allowed.add(dep_lower)
            if dep_lower in _PACKAGE_TO_IMPORT_NAME:
                allowed.add(_PACKAGE_TO_IMPORT_NAME[dep_lower])
        # Los otros archivos del propio proyecto son imports legítimos
        # (from utils import X), no dependencias de terceros -- sin esto,
        # CADA test que importa de otro archivo del proyecto se marcaba
        # como "paquete no declarado" (medido en producción real).
        for file_spec in (project_files or []):
            name = file_spec.get("path", "").rsplit("/", 1)[-1]
            if name.endswith(".py"):
                allowed.add(name[:-3].lower())
        return allowed

    def _find_undeclared_dependencies(self, code: str, allowed_packages: set) -> list:
        """
        Detecta, de forma determinista (AST, no LLM), imports o fixtures de test
        que requieren un paquete no declarado en plan["dependencies"]. Es el
        árbitro objetivo para _generate_validated_file_content: si esta lista
        no está vacía, el contenido NO se escribe, se pide reescritura.
        """
        violations = []
        imports = self._extract_imports(code)
        for mod in imports:
            mod_lower = mod.lower()
            if mod_lower in sys.stdlib_module_names:
                continue
            if mod_lower in allowed_packages:
                continue
            violations.append(mod)

        fixtures = self._extract_test_fixture_names(code)
        for fixture_name, required_pkg in _KNOWN_UNDECLARED_FIXTURES.items():
            if fixture_name in fixtures and required_pkg.lower() not in allowed_packages:
                violations.append(f"{fixture_name} (fixture, requiere {required_pkg})")

        return violations

    # Globals implícitos que todo módulo Python tiene sin necesidad de import
    # ni definición explícita (ej: `if __name__ == "__main__":`).
    _MODULE_LEVEL_IMPLICIT_GLOBALS = frozenset({
        "__name__", "__file__", "__doc__", "__package__", "__spec__",
        "__loader__", "__builtins__", "__annotations__", "__dict__", "__class__",
    })

    def _find_undefined_names(self, code: str) -> set:
        """
        Detecta, vía AST, nombres usados (Load) que no están importados,
        definidos ni son builtins/globals implícitos en ningún punto del
        archivo. Deliberadamente permisivo (unión de todo el archivo, sin
        resolución de scopes) para minimizar falsos positivos -- prioriza no
        romper tests correctos por sobre atrapar cada caso límite. Ataca un
        patrón de fallo recurrente medido en 3 casos reales independientes:
        NameError por un nombre (csv, factorial, Mock) usado sin importar.

        Limitaciones deliberadas (no extender sin repetir esta validación):
        - No hace resolución completa de ámbitos léxicos (una definición en
          cualquier función "cuenta" para todo el archivo).
        - No infiere símbolos creados dinámicamente (`globals()`, `exec()`,
          `setattr()`, etc.) -- estos pueden dar falsos negativos.
        - Está pensado para atrapar errores comunes de generación (import
          faltante, typo), no para demostrar corrección semántica del
          programa.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return set()

        defined = set(dir(builtins)) | self._MODULE_LEVEL_IMPLICIT_GLOBALS
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    defined.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    defined.add(alias.asname or alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
                for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                    defined.add(arg.arg)
                if node.args.vararg:
                    defined.add(node.args.vararg.arg)
                if node.args.kwarg:
                    defined.add(node.args.kwarg.arg)
            elif isinstance(node, ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for n in ast.walk(target):
                        if isinstance(n, ast.Name):
                            defined.add(n.id)
            elif isinstance(node, ast.With):
                for item in node.items:
                    if isinstance(item.optional_vars, ast.Name):
                        defined.add(item.optional_vars.id)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                for n in ast.walk(node.target):
                    if isinstance(n, ast.Name):
                        defined.add(n.id)
            elif isinstance(node, ast.ExceptHandler):
                if node.name:
                    defined.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                defined.update(node.names)
            elif isinstance(node, ast.comprehension):
                for n in ast.walk(node.target):
                    if isinstance(n, ast.Name):
                        defined.add(n.id)
            elif isinstance(node, ast.Lambda):
                for arg in node.args.args + node.args.kwonlyargs:
                    defined.add(arg.arg)

        used = {
            n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        return used - defined

    def _extract_failing_files(self, output: str) -> set:
        """
        Extrae, de forma determinista, los archivos que pytest señala como
        origen real de cada falla (líneas 'archivo.py:N: Excepcion' que
        pytest siempre imprime al pie de cada traceback). Es el hecho
        objetivo contra el que se valida el diagnóstico del LLM en
        FIX_DECIDE_PROMPT -- evita que el fixer corrija un archivo que no
        tiene nada que ver con el fallo real (medido: 2 de 3 casos reales
        de validación en Telegram mostraron este mismatch).
        """
        return set(re.findall(r'([\w./\\-]+\.py):\d+:', output))

    def _resolve_file_path(self, raw_path: str, valid_paths: set) -> str | None:
        """Normaliza una ruta que el LLM puede devolver como /workspace/x.py, ./x.py, etc."""
        normalized = raw_path
        for prefix in ("/workspace/", "workspace/", "./", "/"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
        if normalized in valid_paths:
            return normalized
        basename = normalized.split("/")[-1]
        return next(
            (p for p in valid_paths if p == basename or p.endswith("/" + basename)),
            None,
        )

    async def _create_plan(self, request: str) -> dict | None:
        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": PLAN_PROMPT.format(request=request)}],
                max_tokens=600,
                structured=True,
                task_type="coding",
            )
            return extract_json(raw, expect="object")
        except Exception as e:
            logger.error(f"Plan generation failed: {e}")
            return None

    async def _generate_file_content(self, plan: dict, file_spec: dict, source_code: dict | None = None) -> str:
        try:
            context_lines = [
                f"- {f['path']}: {f['purpose']}" for f in plan["files"]
            ]
            context = "\n".join(context_lines)

            # Si nos pasan el código fuente real (caso: generando un test), lo
            # incluimos COMPLETO en el contexto. El test debe verificar la interfaz
            # que el código realmente expone, no la que el plan sugería en abstracto.
            if source_code:
                code_blocks = "\n\n".join(
                    f"=== {path} (código real, escribe tus tests contra ESTA interfaz) ===\n{content}"
                    for path, content in source_code.items()
                )
                context += (
                    "\n\nSOURCE CODE ALREADY WRITTEN — your tests MUST import and test "
                    "exactly what these files expose (same function/class/method names, "
                    "same signatures). Do NOT invent functions that don't exist here:\n\n"
                    + code_blocks
                )

            deps = plan.get("dependencies") or []
            deps_block = "\n".join(f"- {d}" for d in deps) if deps else "(none -- only Python's standard library is available)"

            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": CODE_PROMPT.format(
                    project_name=plan["project_name"],
                    description=plan["description"],
                    file_path=file_spec["path"],
                    purpose=file_spec["purpose"],
                    dependencies=deps_block,
                    context=context,
                )}],
                max_tokens=2000,
                task_type="coding",
            )

            # Limpiar code fences si el LLM las incluyó
            content = raw.strip()
            content = re.sub(r'^```[a-z]*\n', '', content)
            content = re.sub(r'\n```$', '', content)
            return content
        except Exception as e:
            logger.error(f"File generation failed: {e}")
            return f"# Error generating {file_spec['path']}: {e}\n"

    def _validate_content(self, plan: dict, content: str) -> ContentViolations:
        """
        Corre las validaciones deterministas (sintaxis + dependencias no
        declaradas + nombres no definidos) y devuelve la violación combinada.
        Solo detecta -- no corrige ni reintenta. El presupuesto de reintentos
        lo controla quien la invoca (el loop de _generate_validated_file_content,
        o el loop ya existente de _run_with_tests/_fix_error). Nunca introduce
        un ciclo nuevo.
        """
        try:
            ast.parse(content)
        except SyntaxError as e:
            # Un error de sintaxis hace que los demás chequeos AST fallen
            # silenciosamente igual (todos capturan SyntaxError y devuelven
            # vacío) -- se reporta directamente, sin correr los demás.
            return ContentViolations(
                syntax_error={"msg": e.msg, "lineno": e.lineno, "offset": e.offset}
            )

        project_files = (plan.get("files") or []) + (plan.get("test_files") or [])
        allowed = self._build_allowed_packages(plan.get("dependencies") or [], project_files)
        return ContentViolations(
            undeclared_dependencies=self._find_undeclared_dependencies(content, allowed),
            undefined_names=sorted(self._find_undefined_names(content)),
        )

    async def _correct_content(
        self, file_path: str, content: str, violations: ContentViolations, plan: dict
    ) -> str:
        """Una única corrección (una sola llamada LLM) sobre las violaciones detectadas."""
        deps = plan.get("dependencies") or []
        deps_block = "\n".join(f"- {d}" for d in deps) if deps else "(none -- only Python's standard library is available)"
        raw, _ = await llm.complete(
            messages=[{"role": "user", "content": FIX_CONTENT_VIOLATIONS_PROMPT.format(
                file_path=file_path,
                current_content=content,
                violations=violations.as_text(),
                dependencies=deps_block,
            )}],
            max_tokens=2000,
            task_type="coding",
        )
        corrected = raw.strip()
        corrected = re.sub(r'^```[a-z]*\n', '', corrected)
        corrected = re.sub(r'\n```$', '', corrected)
        return corrected

    async def _generate_validated_file_content(
        self, plan: dict, file_spec: dict, source_code: dict | None = None, max_attempts: int = 2
    ) -> str:
        """
        Genera el contenido de un archivo y lo valida (_validate_content) antes
        de devolverlo, corrigiendo (_correct_content) hasta max_attempts veces
        si hace falta.

        Por qué existe esto además del prompt reforzado: la generación es
        probabilística (el modelo puede alucinar una librería de mocking no
        instalada, o usar un nombre sin importarlo, incluso con la instrucción
        explícita -- medido ~25-30% de casos residuales). Ambas propiedades
        SÍ son verificables con código, así que el árbitro final es
        determinista, no el prompt.
        """
        content = await self._generate_file_content(plan, file_spec, source_code=source_code)

        for _ in range(max_attempts):
            violations = self._validate_content(plan, content)
            if not violations:
                break
            content = await self._correct_content(file_spec["path"], content, violations, plan)

        remaining = self._validate_content(plan, content)
        if remaining:
            logger.warning(
                f"⚠️ {file_spec['path']}: violaciones persisten tras "
                f"{max_attempts} intentos correctivos: {remaining.as_text()}"
            )
        return content

    async def _fix_error(
        self,
        sandbox: AgentSandbox,
        plan: dict,
        error_result,
        semantic_hint: str = "",
        test_failures: str = "",
    ) -> bool:
        """
        Corrige el problema en dos llamadas LLM:
        1) Diagnóstico (JSON pequeño): qué archivo modificar y por qué
        2) Reescritura (texto plano): el contenido completo del archivo

        El fixer ve TODOS los archivos del proyecto (código + tests). Cuando hay
        test_failures, esos guían la corrección como contrato objetivo. El LLM
        puede elegir modificar un archivo de test si ese estuviera mal escrito,
        pero el prompt prioriza arreglar el código fuente primero.

        Cuando NO hay test_failures (flujo no-testable), usa error_result y
        semantic_hint como feedback alternativo.
        """
        # Leer TODOS los archivos del proyecto (código + tests)
        all_files_content = {}
        plan_files = plan.get("files", []) or []
        plan_test_files = plan.get("test_files", []) or []
        for file_spec in plan_files + plan_test_files:
            content = sandbox.read_file(file_spec["path"])
            if content is not None:
                all_files_content[file_spec["path"]] = content

        if not all_files_content:
            return False

        all_files_str = "\n\n".join(
            f"=== {path} ===\n{content}"
            for path, content in all_files_content.items()
        )

        # Construir el feedback principal
        if test_failures:
            # Modo guiado por tests: el feedback es la salida estructurada de pytest
            primary_feedback = test_failures
            additional_context = ""
        else:
            # Modo legacy (no testable): error de ejecución + hint semántico
            feedback_parts = []
            if error_result and error_result.output:
                feedback_parts.append(f"Execution output / traceback:\n{error_result.output[:2000]}")
            if semantic_hint:
                feedback_parts.append(f"Semantic problem with the output: {semantic_hint}")
            primary_feedback = "\n\n".join(feedback_parts) or "Unknown issue"
            additional_context = (
                "Note: this project has no automated tests. Diagnose from the execution "
                "output above and from the source code."
            )

        # ── PASO 1: DIAGNÓSTICO ──
        try:
            raw_decision, _ = await llm.complete(
                messages=[{"role": "user", "content": FIX_DECIDE_PROMPT.format(
                    all_files=all_files_str[:12000],
                    test_failures=primary_feedback,
                    additional_context=additional_context,
                )}],
                max_tokens=500,
                structured=True,
                task_type="coding",
            )

            logger.info(f"=== RAW FIX_DECIDE ===\n{raw_decision[:800]}\n{'='*80}")

            decision = extract_json(raw_decision, expect="object")
            if not decision:
                logger.debug("Fix decide: no parseable JSON")
                return False

            file_to_fix = decision.get("file_to_fix", "").strip()
            diagnosis = decision.get("diagnosis", "").strip()
            what_to_change = decision.get("what_to_change", "").strip()
            is_test_file = bool(decision.get("is_test_file", False))

            if not file_to_fix:
                logger.debug("Fix decide: missing file_to_fix")
                return False

            valid_paths = set(all_files_content.keys())
            resolved = self._resolve_file_path(file_to_fix, valid_paths)
            if not resolved:
                logger.warning(f"⚠️  File '{file_to_fix}' not in project. Valid: {valid_paths}")
                return False
            file_to_fix = resolved

            # Validación determinista: el traceback real de pytest es el hecho
            # objetivo de qué archivo causó la excepción. Un solo intento de
            # reconsideración acotado con esa evidencia si el LLM eligió otra
            # cosa -- nunca bloquea, nunca reintenta indefinidamente.
            if test_failures:
                # Filtramos contra los archivos reales del proyecto: un traceback de
                # error de sintaxis/colección puede incluir rutas internas del
                # intérprete/pytest (ast.py, _pytest/python.py) que el regex también
                # captura -- sin este filtro disparan reconsideraciones espurias
                # (inofensivas por el diseño fail-open, pero ruido evitable).
                valid_basenames = {p.split("/")[-1] for p in valid_paths}
                failing_files = {
                    f for f in self._extract_failing_files(primary_feedback)
                    if f.split("/")[-1] in valid_basenames
                }
                chosen_basename = file_to_fix.split("/")[-1]
                matches_traceback = any(
                    chosen_basename == f.split("/")[-1] for f in failing_files
                )
                if failing_files and not matches_traceback:
                    logger.warning(
                        f"⚠️ FIX_DECIDE eligió '{file_to_fix}' pero el traceback "
                        f"real señala {failing_files} -- pidiendo reconsideración"
                    )
                    try:
                        raw_retry, _ = await llm.complete(
                            messages=[{"role": "user", "content": FIX_DECIDE_RECONSIDER_PROMPT.format(
                                previous_file=file_to_fix,
                                previous_diagnosis=diagnosis,
                                failing_files=", ".join(sorted(failing_files)),
                                all_files=all_files_str[:12000],
                                test_failures=primary_feedback,
                            )}],
                            max_tokens=500,
                            structured=True,
                            task_type="coding",
                        )
                        retry_decision = extract_json(raw_retry, expect="object")
                        retry_file = (retry_decision or {}).get("file_to_fix", "").strip()
                        retry_resolved = self._resolve_file_path(retry_file, valid_paths) if retry_file else None
                        if retry_resolved:
                            file_to_fix = retry_resolved
                            diagnosis = retry_decision.get("diagnosis", diagnosis).strip() or diagnosis
                            what_to_change = retry_decision.get("what_to_change", what_to_change).strip() or what_to_change
                            is_test_file = bool(retry_decision.get("is_test_file", is_test_file))
                    except Exception as e:
                        logger.debug(f"Fix decide reconsider failed: {e}")

        except Exception as e:
            logger.debug(f"Fix decide failed: {e}")
            return False

        # ── PASO 2: REESCRITURA (texto plano) ──
        try:
            current_content = all_files_content.get(file_to_fix, "")
            other_files = {p: c for p, c in all_files_content.items() if p != file_to_fix}
            other_files_str = "\n\n".join(
                f"=== {path} ===\n{content}"
                for path, content in other_files.items()
            ) if other_files else "(none — single file project)"

            raw_content, _ = await llm.complete(
                messages=[{"role": "user", "content": FIX_REWRITE_PROMPT.format(
                    file_to_fix=file_to_fix,
                    current_content=current_content,
                    other_files=other_files_str[:6000],
                    test_failures=primary_feedback[:2000],
                    diagnosis=diagnosis or "fix the failing tests",
                    what_to_change=what_to_change or "apply the minimal fix needed",
                )}],
                max_tokens=8000,
                task_type="coding",
            )

            logger.info(f"=== RAW FIX_REWRITE (first 600) ===\n{raw_content[:600]}\n{'='*80}")

            new_content = raw_content.strip()
            new_content = re.sub(r'^```[a-z]*\n', '', new_content)
            new_content = re.sub(r'\n```$', '', new_content)

            if not new_content.strip():
                logger.debug("Fix rewrite produced empty content")
                return False

            # Validación de una sola pasada, SIN loop propio: si la
            # reescritura introduce una violación (dependencia no declarada o
            # nombre no definido -- medido: el fixer introdujo un 'main' sin
            # importar en una reescritura real), un único intento de
            # corrección. El ciclo de reintentos real ya existe en
            # _run_with_tests (invoca _fix_error hasta max_attempts veces con
            # detección de estancamiento) -- si la violación persiste, se
            # escribe igual y pytest la expone como fallo en la siguiente
            # vuelta de ESE ciclo, sin presupuesto nuevo ni bucle anidado.
            violations = self._validate_content(plan, new_content)
            if violations:
                new_content = await self._correct_content(file_to_fix, new_content, violations, plan)

            target_icon = "🧪" if is_test_file else "🔧"
            logger.info(f"{target_icon} Fixing {file_to_fix}: {diagnosis[:120]}")
            return sandbox.write_file(file_to_fix, new_content)

        except Exception as e:
            logger.debug(f"Fix rewrite failed: {e}")
            return False

    async def _validate_output(
        self,
        request: str,
        output: str,
        sandbox: AgentSandbox,
        plan: dict,
    ) -> dict:
        """
        Valida semánticamente revisando: petición + código completo + output.
        El LLM razona sobre coherencia código↔output↔petición.
        Si falla la validación del LLM, devuelve satisfies=True (no bloquea al usuario).
        """
        if not output or not output.strip():
            return {
                "satisfies": False,
                "issue": "the program produced no output",
                "fix_hint": "ensure the program prints its results to stdout",
            }

        # Leer todos los archivos del proyecto para que el validador vea el contexto completo
        files_content = []
        for file_spec in plan["files"]:
            content = sandbox.read_file(file_spec["path"])
            if content is not None:
                files_content.append(f"=== {file_spec['path']} ===\n{content}")
        all_files_str = "\n\n".join(files_content)[:6000]

        try:
            raw, _ = await llm.complete(
                messages=[{"role": "user", "content": VALIDATE_PROMPT.format(
                    request=request[:500],
                    all_files=all_files_str,
                    output=output[:2000],
                )}],
                max_tokens=300,
                structured=True,
                task_type="coding",
            )
            data = extract_json(raw, expect="object")
            if not data:
                return {"satisfies": True, "issue": "", "fix_hint": ""}
            return {
                "satisfies": bool(data.get("satisfies", True)),
                "issue": str(data.get("issue", "")),
                "fix_hint": str(data.get("fix_hint", "")),
            }
        except Exception as e:
            logger.debug(f"Output validation failed: {e}")
            return {"satisfies": True, "issue": "", "fix_hint": ""}

    # Veredictos que significan ÉXITO REAL verificado. Cualquier otro = NO verificado.
    # Honestidad innegociable: nunca declarar "listo" si los tests no pasaron de verdad.
    _SUCCESS_VERDICTS = frozenset({
        "tests_pass", "tests_pass_and_runs", "tests_pass_run_warn", "semantic_pass",
    })

    async def _build_summary(self, plan: dict, files: dict, verdict: str) -> str:
        file_list = "\n".join(f"• `{p}`" for p in sorted(files.keys()))
        run_cmd = plan.get("run_command", "")

        if verdict in self._SUCCESS_VERDICTS:
            header = f"✅ *{plan['project_name']}*"
            note_en = {
                "tests_pass_and_runs": "All tests pass and the program runs correctly.",
                "tests_pass": "All tests pass.",
                "tests_pass_run_warn": (
                    "All tests pass (verified by the test suite). "
                    "Note: the dry-run sanity check reported an error — if the program "
                    "asks for keyboard input this is expected; check the execution log."
                ),
                "semantic_pass": "Verified: the program does what was requested.",
            }.get(verdict, "Verified.")
        else:
            header = f"⚠️ *{plan['project_name']}*"
            note_en = {
                "tests_failing": "I could not get the tests to pass. The project may have errors — review it before using it.",
                "tests_pass_run_fails": "Tests pass, but the program fails when run. Check the log before using it.",
                "semantic_unverified": "I could not verify that the result is correct. Review it before using it.",
            }.get(verdict, "I could not verify the project. Review it before using it.")

        # Nota sustancial (no una etiqueta de UI): se genera en el idioma objetivo
        # del turno cuando se conoce; si no (job en background sin turno activo),
        # se entrega en inglés — mismo default seguro que el resto del proyecto
        # cuando no hay idioma objetivo que forzar.
        lang = get_target_language()
        note = note_en
        if lang and lang != "en":
            translated, _ = await llm.complete(
                messages=[{"role": "user", "content": note_en}],
                system=(
                    "Rewrite this status message naturally, preserving its exact meaning. "
                    "Output ONLY the rewritten text, nothing else." + _lang_directive(lang)
                ),
                max_tokens=120,
                enforce_language=True,
                task_type="memory",
            )
            note = (translated or note_en).strip()

        return (
            f"{header}\n\n"
            f"{plan['description']}\n\n"
            f"⚙️ {note}\n\n"
            f"📎\n{file_list}\n\n"
            f"`{run_cmd}`"
        )
