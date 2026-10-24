"""Planejamento de tarefas e validacao de etapas e dependencias."""

import os
import re
import json
import shlex
import unicodedata

from . import api, config, search


CURRENT_WEB_FACT = re.compile(
    r"\b(?:atual|atualmente|hoje|agora|mais recente|mais novo|mais nova)\b"
    r".{0,65}\b(?:presidente|primeiro-ministro|primeira-ministra|cotacao|dolar|"
    r"preco|valor|versao|release|placar|resultado|noticia|noticias)\b|"
    r"\b(?:presidente|primeiro-ministro|primeira-ministra|cotacao|dolar|preco|"
    r"valor|versao|release|placar|resultado|noticia|noticias)\b"
    r".{0,65}\b(?:atual|atualmente|hoje|agora|mais recente|mais novo|mais nova)\b",
    re.IGNORECASE,
)


def _build_action_plan(user_instruction: str) -> dict:
    """Create explicit task phases and mark any research prerequisite before commands."""
    research_required = _requires_action_web_search(user_instruction)
    directory_name_from_research = bool(
        research_required
        and re.search(r"\b(?:pasta|diret[oó]rio)\b", user_instruction, re.IGNORECASE)
        and re.search(
            r"\b(?:com o nome|nome dele|nome dela|usando o nome|nome do atual|nome do)\b",
            user_instruction,
            re.IGNORECASE,
        )
    )
    requested_directory = ""
    if directory_name_from_research:
        directory_match = re.search(
            r"\b(?:em|dentro de)\s+(/[^\s,;]+)",
            user_instruction,
            re.IGNORECASE,
        )
        if directory_match:
            requested_directory = directory_match.group(1).rstrip(".,!?")
    if research_required:
        steps = [
            "Pesquisar na web a informação solicitada e guardar as fontes como evidência.",
            "Usar o resultado da pesquisa para definir a ação dependente; não inferir dados ausentes.",
            "Propor o comando exato e aguardar a confirmação do usuário.",
            "Verificar o resultado real do comando antes de declarar a tarefa concluída.",
        ]
        if directory_name_from_research:
            steps[1] = (
                "Usar o nome próprio confirmado nos resultados para montar o caminho da pasta solicitada; "
                "não substituir essa etapa por verificações locais sem relação."
            )
    else:
        steps = [
            "Identificar quais informações do sistema são necessárias para a tarefa.",
            "Propor o comando exato e aguardar a confirmação do usuário.",
            "Verificar o resultado real do comando antes de declarar a tarefa concluída.",
        ]
    return {
        "steps": steps,
        "research_required": research_required,
        "research_attempted": False,
        "research_has_evidence": False,
        "research_entities": [],
        "directory_name_from_research": directory_name_from_research,
        "requested_directory": requested_directory,
    }


def _needs_task_decomposition(text: str) -> bool:
    """Identify requests that explicitly combine multiple actions or dependencies."""
    if re.search(
        r"\b(?:e|and|then|also|depois|em seguida|ap[oó]s|antes de|e ent[aã]o|bem como)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    action_pattern = re.compile(
        r"\b(?:pesquise|pesquisar|busque|buscar|consulte|consultar|verifique|verificar|"
        r"inspecione|inspecionar|investigue|investigar|descubra|descobrir|analise|analisar|"
        r"confira|conferir|compare|comparar|teste|testar|valide|validar|leia|ler|resuma|resumir|"
        r"crie|criar|fa[cç]a|fazer|execute|executar|"
        r"instale|instalar|baixe|baixar|clone|clonar|configure|configurar|reinicie|reiniciar|"
        r"inicie|iniciar|pare|parar|mova|mover|copie|copiar|extraia|extrair|atualize|atualizar)\b",
        re.IGNORECASE,
    )
    action_matches = action_pattern.findall(text)
    has_research = bool(search.EXPLICIT_RESEARCH_REQUEST.search(text))
    has_action = bool(action_matches)
    if len(action_matches) >= 2 and re.search(r"\be\b", text, re.IGNORECASE):
        return True
    return bool(
        has_research
        and has_action
        and re.search(r"\be\b", text, re.IGNORECASE)
    )


def _requires_planned_command(text: str) -> bool:
    """Detect an explicit system action that must be represented in the task graph."""
    return bool(re.search(
        r"\b(?:crie|criar|fa[cç]a|fazer|execute|executar|instale|instalar|baixe|baixar|"
        r"clone|clonar|configure|configurar|reinicie|reiniciar|inicie|iniciar|pare|parar|"
        r"mova|mover|copie|copiar|extraia|extrair|atualize|atualizar|remova|remover|"
        r"apague|apagar|delete|deletar|verifique|verificar|inspecione|inspecionar|"
        r"investigue|investigar|liste|listar|edite|editar)\b",
        text,
        re.IGNORECASE,
    ))


def _simple_directory_creation_action(text: str) -> str:
    """Build an exact mkdir action for a clear, single-step named-directory request."""
    if _requires_action_web_search(text) or _needs_task_decomposition(text):
        return ""
    if not re.search(
        r"\b(?:crie|criar|cria|fa[cç]a|fazer)\b.{0,60}\b(?:pasta|diret[oó]rio)\b",
        text,
        re.IGNORECASE,
    ):
        return ""

    name_match = re.search(
        r"\b(?:pasta|diret[oó]rio)\b.{0,50}?\b(?:chamad[ao]|nomead[ao]|com (?:o )?nome)\s+"
        r"(?:['\"`]([^'\"`]+)['\"`]|(.+?))(?=\s+(?:em|dentro de)\s+/|[,;.!?]|$)",
        text,
        re.IGNORECASE,
    )
    path_match = re.search(r"\b(?:em|dentro de)\s+(/[^\s,;]+)", text, re.IGNORECASE)
    if not name_match or not path_match:
        return ""
    name = (name_match.group(1) or name_match.group(2) or "").strip().strip("'\"`.,;:!? ")
    parent = path_match.group(1).rstrip(".,!?/") or "/"
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        return ""

    target = os.path.join(parent, name)
    command = "mkdir -p -- " + shlex.quote(target)
    return (
        "<<<ACTION>>>\n"
        f"Comando: {command}\n"
        "Finalidade: criar a pasta com o nome e no diretório pedidos pelo usuário"
    )


def _validate_task_plan(plan_data: dict) -> tuple[list[dict] | None, str]:
    """Validate a small dependency graph returned by the task planner."""
    if not isinstance(plan_data, dict) or not isinstance(plan_data.get("steps"), list):
        return None, "O plano precisa conter uma lista de etapas."
    raw_steps = plan_data["steps"]
    if not raw_steps or len(raw_steps) > 8:
        return None, "O plano precisa ter entre 1 e 8 etapas."
    normalized = []
    ids = set()
    for step in raw_steps:
        if not isinstance(step, dict):
            return None, "Cada etapa precisa ser um objeto estruturado."
        step_id = str(step.get("id", "")).strip()
        kind = str(step.get("kind", "")).strip().lower()
        goal = str(step.get("goal", "")).strip()
        dependencies = step.get("depends_on", [])
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,19}", step_id) or step_id in ids:
            return None, "Os identificadores das etapas precisam ser únicos e simples."
        if kind not in {"research", "command"} or not goal:
            return None, "Cada etapa precisa ter tipo research ou command e um objetivo."
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            return None, "As dependências precisam ser uma lista de identificadores."
        ids.add(step_id)
        normalized.append({
            "id": step_id,
            "kind": kind,
            "goal": goal[:400],
            "depends_on": dependencies,
            "status": "pending",
            "result": "",
        })
    known = {step["id"] for step in normalized}
    for step in normalized:
        if step["id"] in step["depends_on"] or any(dep not in known for dep in step["depends_on"]):
            return None, f"Dependência inválida na etapa {step['id']}."

    by_id = {step["id"]: step for step in normalized}
    visited = set()
    active = set()

    def visit(step_id):
        if step_id in active:
            return False
        if step_id in visited:
            return True
        active.add(step_id)
        for dependency in by_id[step_id]["depends_on"]:
            if not visit(dependency):
                return False
        active.remove(step_id)
        visited.add(step_id)
        return True

    if not all(visit(step["id"]) for step in normalized):
        return None, "O plano contém dependências circulares."
    return normalized, ""


def _plan_task_with_model(
    user_instruction: str,
    system_prompt: str,
    research_required: bool = False,
    command_required: bool = False,
) -> tuple[list[dict] | None, str]:
    planner_messages = [
        {
            "role": "system",
            "content": (
                system_prompt
                + "\n\nDecomponha a solicitação em etapas executáveis com dependências explícitas. "
                "Não revele pensamento interno. Inclua apenas etapas necessárias, em ordem lógica. "
                "Use kind 'research' para pesquisa web e 'command' para uma ação ou verificação no sistema. "
                "Cada etapa precisa descrever um resultado observável. Se uma etapa usa dados de outra, "
                "declare o id anterior em depends_on. Responda somente no formato <<<TASK_PLAN>>> seguido "
                'de JSON: {"steps":[{"id":"s1","kind":"research","goal":"...","depends_on":[]},'
                '{"id":"s2","kind":"command","goal":"...","depends_on":["s1"]}]}. '
                "Use de 1 a 8 etapas e ids s1, s2 etc. Não inclua etapa de resposta final."
                + (
                    " Como o usuário pediu pesquisa explicitamente ou a informação depende de atualidade, inclua uma etapa research e faça as etapas que usam o resultado dependerem dela."
                    if research_required else ""
                )
                + (
                    " O pedido também exige uma ação no sistema; inclua ao menos uma etapa command. Não considere a pesquisa como conclusão do pedido."
                    if command_required else ""
                )
            ),
        },
        {"role": "user", "content": user_instruction},
    ]
    for _ in range(2):
        response = api.query_messages_raw(planner_messages)
        marker = response.find(config.TASK_PLAN_MARKER)
        if marker >= 0:
            raw_json = response[marker + len(config.TASK_PLAN_MARKER):].strip()
            try:
                plan_data = json.loads(raw_json)
            except json.JSONDecodeError:
                error_message = "O JSON do plano está malformado. Responda novamente apenas com o formato solicitado."
            else:
                steps, error_message = _validate_task_plan(plan_data)
                if steps is not None:
                    if research_required and not any(step["kind"] == "research" for step in steps):
                        error_message = "O pedido exige pesquisa web; inclua uma etapa research e dependências para as ações que usam o resultado."
                    elif command_required and not any(step["kind"] == "command" for step in steps):
                        error_message = "O pedido também exige uma ação no sistema; inclua uma etapa command dependente das informações pesquisadas."
                    else:
                        return steps, ""
        else:
            error_message = "Faltou o marcador <<<TASK_PLAN>>>. Responda somente com o plano estruturado solicitado."
        planner_messages.extend([
            {"role": "assistant", "content": response},
            {"role": "user", "content": error_message},
        ])
    return None, "Não consegui validar um plano estruturado para esta tarefa. Nenhuma ação foi iniciada."


def _ready_task_steps(task_steps: list[dict]) -> list[dict]:
    completed = {step["id"] for step in task_steps if step["status"] == "complete"}
    return [
        step for step in task_steps
        if step["status"] in {"pending", "failed"} and set(step["depends_on"]).issubset(completed)
    ]


def _format_task_steps(task_steps: list[dict]) -> str:
    return "\n".join(
        f"{index}. [{step['id']}] ({step['kind']}; {step['status']}) {step['goal']}"
        + (f" — depende de: {', '.join(step['depends_on'])}" if step["depends_on"] else "")
        + (f" — resultado anterior: {step['result'][:240]}" if step.get("result") else "")
        for index, step in enumerate(task_steps, 1)
    )


def _task_step_violation(response: str, task_steps: list[dict]) -> str:
    """Require every proposed action to target a currently ready plan step of the right kind."""
    ready = _ready_task_steps(task_steps)
    step_match = re.search(r"(?:^|\n)Etapa:\s*([a-z][a-z0-9_-]{0,19})\b", response, re.IGNORECASE)
    if not step_match:
        return "A proposta precisa identificar a etapa do plano usando 'Etapa: id'."
    step_id = step_match.group(1)
    step = next((item for item in task_steps if item["id"] == step_id), None)
    if step is None:
        return "A proposta referencia uma etapa que não existe no plano."
    if step not in ready:
        return "Essa etapa ainda não está pronta; conclua primeiro as dependências pendentes."
    if step["kind"] != "command":
        return "Esta etapa exige pesquisa web; não proponha um comando de sistema para ela."
    return ""


def _task_step_id_from_response(response: str) -> str:
    match = re.search(r"(?:^|\n)Etapa:\s*([a-z][a-z0-9_-]{0,19})\b", response, re.IGNORECASE)
    return match.group(1) if match else ""


def _requires_action_web_search(text: str) -> bool:
    """Detect explicit web requests and time-sensitive facts that need an external source."""
    explicit_matches = list(search.EXPLICIT_RESEARCH_REQUEST.finditer(text))
    for match in explicit_matches:
        prefix = text[max(0, match.start() - 30):match.start()]
        if not re.search(r"\b(?:n[aã]o|nunca)\s+(?:(?:quero|precisa|deve|deveria)\s+)?$", prefix, re.IGNORECASE):
            return True
    return bool(
        CURRENT_WEB_FACT.search(text)
        or search.CURRENT_FACT_QUERY.search(text)
        or search.WEATHER_QUERY.search(text)
    )


def _action_plan_prompt(plan: dict) -> str:
    lines = ["Plano de execução obrigatório:"]
    lines.extend(f"{index}. {step}" for index, step in enumerate(plan["steps"], 1))
    if plan["research_required"]:
        lines.append(
            "A pesquisa é uma dependência obrigatória: não proponha comandos nem uma resposta final "
            "até receber resultados relacionados da ferramenta web."
        )
    if plan.get("directory_name_from_research"):
        lines.append(
            "A ação dependente é criar a pasta pedida usando um nome próprio dos resultados. "
            "Não aceite nem proponha comandos sem relação, como whoami ou leitura de arquivos locais."
        )
    return "\n".join(lines)


def _extract_research_entities(text: str) -> list[str]:
    """Extract multiword proper names from the validated search context for action checks."""
    pattern = re.compile(
        r"\b[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+(?:[ -]+[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+){1,3}\b"
    )
    entities = []
    for match in pattern.finditer(text):
        candidate = match.group(0).strip()
        if candidate not in entities:
            entities.append(candidate)
    return entities


def _normalize_for_action_check(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", text.casefold())
        if unicodedata.category(char) != "Mn"
    )


def _extract_proposed_action_command(response: str) -> str:
    if "<<<ACTION>>>" not in response:
        return ""
    action_block = response.split("<<<ACTION>>>", 1)[1].strip()
    command_match = re.search(
        r"Comando:\s*(.*?)(?:\nFinalidade:|\n\n|\Z)",
        action_block,
        re.DOTALL | re.IGNORECASE,
    )
    return command_match.group(1).strip().strip("` \n\r") if command_match else ""


def _action_plan_violation(response: str, plan: dict) -> str:
    """Reject dependent commands that do not use the researched entity and requested destination."""
    if not plan.get("directory_name_from_research"):
        return ""
    command = _extract_proposed_action_command(response)
    if not command:
        return ""
    try:
        parts = shlex.split(command)
    except ValueError:
        return "Não consegui interpretar o comando com segurança. Proponha apenas o comando mkdir necessário."
    if not parts or parts[0] != "mkdir":
        return "A etapa atual exige criar a pasta solicitada; este comando não a cria."
    if any(part in {"&&", "||", ";", "|", ">", "<"} for part in parts):
        return "A etapa atual deve conter somente o comando para criar a pasta, sem ações adicionais."
    options = [part for part in parts[1:] if part.startswith("-")]
    targets = [part for part in parts[1:] if not part.startswith("-")]
    if any(option not in {"-p", "--parents"} for option in options) or len(targets) != 1:
        return "Proponha somente mkdir -p com o caminho exato da pasta solicitada."
    requested_directory = plan.get("requested_directory", "")
    target = os.path.normpath(targets[0])
    if requested_directory and os.path.dirname(target) != os.path.normpath(requested_directory):
        return f"O comando precisa criar a pasta dentro de {requested_directory}."
    entities = plan.get("research_entities", [])
    if not entities:
        return "Não foi possível extrair um nome próprio dos resultados. Não invente um nome para a pasta."
    if not any(_normalize_for_action_check(os.path.basename(target)) == _normalize_for_action_check(entity) for entity in entities):
        return "O comando não usa nenhum nome próprio confirmado nos resultados da pesquisa."
    return ""
