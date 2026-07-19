# ClawLite — Modelo de Amenazas y Gobernanza de Acciones

**Forgesynapse LTD · v1.0 · junio 2026**

## 1. Propósito y filosofía
ClawLite es un agente de IA **local-first** que ejecuta acciones reales en nombre del
usuario. El principio de seguridad es **default-deny con mediación completa**: ninguna
acción con efecto ocurre salvo que una política explícita la permita bajo un mandato
humano verificable. No afirmamos invulnerabilidad; afirmamos una arquitectura
**auditable, fail-closed y de superficie mínima**, con riesgos residuales declarados.

## 2. Fronteras de confianza
- **Confiable:** el usuario en su canal autenticado (Telegram), el código del kernel, la configuración local.
- **NO confiable (siempre):** todo contenido externo — cuerpos de email, páginas web scrapeadas, PDFs/archivos, resultados de búsqueda. **Nunca** puede originar un mandato.
- **Aislado:** ejecución de código (contenedor Docker). Tratado como hostil por diseño.
- **Semi-confiable:** la salida del LLM. Se trata como **no fiable** (puede divagar, alucinar o ser manipulada) y se valida por código, nunca se obedece a ciegas.

## 3. Activos a proteger
Credenciales/API keys del usuario · cuenta de email y calendario · sistema de archivos
del host · memoria personal · integridad de las acciones (que solo se haga lo que el
usuario mandó).

## 4. Superficie de acción ("las puertas") y su tier de riesgo

| Acción | Riesgo | Control |
|---|---|---|
| Enviar email | ALTO | Mediada por kernel · aprobación humana · chequeo de inyección fail-closed |
| Crear evento de calendario | ALTO | Mediada · aprobación humana |
| Ejecutar código | ALTO | Sandbox Docker aislado · mediada por kernel |
| Borrar memoria | ALTO | Aprobación humana |
| Mensaje proactivo (el agente inicia) | MEDIO | Solo desde tarea recurrente previamente autorizada |
| Leer email / calendario / web | BAJO | Mediada y auditada; entrada tratada como no confiable |

## 5. Amenazas principales y mitigaciones
1. **Inyección de prompt que dispara una acción** (un email dice "reenvía tus datos a X").
   → *Mitigación:* el contenido externo se marca como dato, no instrucción (`wrap_untrusted`);
   y el kernel **prohíbe que un origen externo autorice cualquier acción** (matriz default-deny). Validado.
2. **Sobre-confianza / desalineación del modelo** (la IA decide actuar "por su cuenta").
   → *Mitigación:* el modelo solo PROPONE; el kernel exige **mandato humano explícito** para
   todo lo de alto impacto. El humano MANDA, el kernel EJECUTA.
3. **Fuga de secretos** (una clave pegada en el chat acaba en la memoria/logs).
   → *Mitigación:* redacción de secretos por formato antes de persistir; vault cifrado; las
   claves nunca tocan el modelo.
4. **Acción no contemplada / nueva ruta sin proteger.**
   → *Mitigación:* **default-deny** — cualquier acción no registrada en el kernel se deniega por definición.
5. **Fallo o estado ambiguo abre una acción.**
   → *Mitigación:* **fail-closed** — cualquier error, timeout o estado no verificable resulta en denegar.
6. **Ejecución de código maliciosa.**
   → *Mitigación:* aislamiento en contenedor Docker; tratado como hostil.

## 6. Garantías del kernel de gobernanza (`ActionGuard`)
- **Mediación completa:** toda acción con efecto pasa por `authorize()`.
- **Default-deny por capacidad:** registro explícito acción→política; lo no registrado se deniega.
- **Vínculo de mandato:** origen externo nunca autoriza; alto impacto exige aprobación humana.
- **Fail-closed:** ante cualquier excepción, deniega.
- **Auditable:** registro append-only de cada solicitud, decisión, mandato y motivo (secretos redactados).

Implementación: `clawlite/governance/action_guard.py`. Registro auditable: `data/audit_log.jsonl`.

## 7. Riesgos residuales (declarados con honestidad)
- El **modelo local pequeño** es falible; por eso la seguridad NO depende de su comportamiento,
  sino de redes deterministas alrededor. Su criterio (p.ej. detección de inyección) es una capa
  de apoyo, no el muro — el muro es la aprobación humana.
- **Docker aísla, no es una garantía absoluta** frente a un atacante con un 0-day de escape de contenedor.
- **Cadena de suministro** (dependencias) y **seguridad del host / OAuth** quedan fuera del alcance del kernel.
- La cobertura se está extendiendo puerta a puerta; hasta que todas estén ruteadas, conviven
  controles previos (confirmación por texto) y el kernel.

## 8. Estado
- ✅ Kernel construido y validado (matriz de decisión 11/11).
- ✅ Separación datos/instrucciones, redacción de secretos, vault, sandbox, chequeo de email fail-closed.
- ✅ **TODAS las acciones consecuentes (ALTO/MEDIO) ruteadas por el kernel:** enviar email
  (validado por las dos caras), crear evento de calendario, borrar memoria, ejecutar código
  (CodingAgent vía `run_coding` **y** la tool MCP), y mensajes proactivos.
- ✅ **Barrido de mediación completa realizado.** Detectó y cerró una puerta real: la tool MCP
  `execute_in_sandbox` ejecutaba código saltándose el kernel; ahora pasa por `authorize()`.
- ✅ **LECTURAS también mediadas y auditadas** (leer email/calendario en `gmail_tool`, búsqueda
  web en SearchTool y en el motor de research). Gateadas en el chokepoint de cada capacidad, así
  que TODO llamador (comandos, brief, watches, workflows) queda cubierto. Las lecturas son BAJO
  riesgo y siempre permitidas; el valor es auditoría + contrato central.
- ✅ **Mediación COMPLETA:** las 10 acciones registradas (5 de acción + 5 de lectura) pasan por el
  kernel. Ninguna acción ni lectura externa lo esquiva. Nota honesta: el origen de `web_search` se
  audita de forma gruesa (USER_DIRECT) — aceptable por ser lectura/egress de bajo riesgo.
