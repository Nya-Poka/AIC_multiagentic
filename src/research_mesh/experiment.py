from __future__ import annotations

import os
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .input_normalization import extract_json_object
from .llm import LLMCompletionRequest, LLMGatewayClient, LLMMessage
from .retrieval_quality import build_search_intent
from .schemas import ResearchRequest


class CompletionClient(Protocol):
    async def complete(self, request: LLMCompletionRequest): ...


class ExperimentPlan(BaseModel):
    """A reviewable experiment protocol rather than a prose placeholder."""

    model_config = ConfigDict(extra="forbid")

    design_type: str = Field(min_length=8)
    causal_scope: str = Field(min_length=8)
    hypothesis: str = Field(min_length=12)
    population: str = Field(min_length=8)
    inclusion_criteria: list[str] = Field(min_length=2)
    exclusion_criteria: list[str] = Field(min_length=2)
    independent_variables: list[str] = Field(min_length=1)
    dependent_variables: list[str] = Field(min_length=1)
    controls: list[str] = Field(min_length=3)
    confounders: list[str] = Field(min_length=3)
    intervention_or_exposure: str = Field(min_length=12)
    comparator: str = Field(min_length=8)
    duration: str = Field(min_length=4)
    sample_size_plan: str = Field(min_length=20)
    allocation: str = Field(min_length=8)
    blinding: str = Field(min_length=8)
    data_collection: list[str] = Field(min_length=3)
    analysis_plan: list[str] = Field(min_length=3)
    missing_data_plan: str = Field(min_length=12)
    reproducibility: list[str] = Field(min_length=4)
    ethics: list[str] = Field(min_length=2)
    steps: list[str] = Field(min_length=6)
    assumptions: list[str] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True
    generator: str


_PLACEHOLDER_TERMS = (
    "需在正式试验前具体化",
    "与研究目标一致",
    "可观察结果指标",
    "研究条件",
    "视情况",
    "根据需要",
    "待定",
    "tbd",
    "to be determined",
)


def contains_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(contains_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(contains_placeholder(item) for item in value)
    if not isinstance(value, str):
        return False
    folded = value.casefold()
    return any(term.casefold() in folded for term in _PLACEHOLDER_TERMS)


def validate_experiment_plan(value: dict[str, Any]) -> ExperimentPlan:
    plan = ExperimentPlan.model_validate(value)
    if contains_placeholder(plan.model_dump(mode="json")):
        raise ValueError("experiment plan contains placeholder language")
    return plan


def experiment_plan_issues(value: Any) -> list[str]:
    """Return hard review failures for incomplete or non-reproducible protocols."""

    if not isinstance(value, dict):
        return ["实验方案不是结构化对象"]
    try:
        plan = validate_experiment_plan(value)
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        return ["实验方案缺少或未满足必需字段：" + "、".join(fields)]
    except ValueError:
        return ["实验方案仍包含待定、需具体化或通用占位内容"]

    issues: list[str] = []
    variables = " ".join(
        [*plan.independent_variables, *plan.dependent_variables]
    ).casefold()
    measurement_terms = (
        "小时",
        "分钟",
        "得分",
        "评分",
        "量表",
        "设备",
        "日志",
        "记录",
        "次数",
        "反应时",
        "比例",
        "浓度",
        "gpa",
        "score",
        "scale",
        "device",
        "unit",
        "time",
    )
    if not any(term in variables for term in measurement_terms):
        issues.append("自变量或因变量没有说明可复核的测量方式、单位、量表或评分")

    sample_size = plan.sample_size_plan.casefold()
    sample_terms = ("样本量", "功效", "α", "alpha", "效应", "失访")
    if sum(term in sample_size for term in sample_terms) < 4:
        issues.append("样本量方案必须说明显著性水平、统计功效、效应量来源和失访处理")

    analysis = " ".join(plan.analysis_plan).casefold()
    analysis_terms = ("模型", "效应", "置信区间", "敏感性", "model", "effect", "confidence")
    if sum(term in analysis for term in analysis_terms) < 3:
        issues.append("统计分析方案必须明确模型、效应量/区间估计和敏感性分析")

    if plan.requires_human_approval is not True:
        issues.append("实验方案必须保留伦理与研究负责人审批")
    return issues


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_assumptions(request: ResearchRequest) -> list[str]:
    assumptions = [
        "研究对象能够自主提供知情同意，且研究仅实施经伦理审查认定的最小风险操作",
        "主要结局、排除规则和分析代码在查看组间结果前冻结",
    ]
    if not request.constraints:
        assumptions.append("用户未提供额外资源或时间约束，实施前需由研究负责人确认可行性")
    return assumptions


def _sleep_academic_plan(request: ResearchRequest) -> ExperimentPlan:
    constraints = list(request.constraints)
    return ExperimentPlan(
        design_type="预注册、平行组、评估者盲法的睡眠延长随机对照试验",
        causal_scope=(
            "仅估计安全睡眠延长方案对短期学习表现的平均处理效应，不把观察到的相关性外推为长期因果效应"
        ),
        hypothesis=(
            "与维持原有作息相比，接受睡眠延长与规律作息指导的大学生在干预期内的客观平均睡眠时长增加，"
            "且标准化课程测验得分改善。"
        ),
        population="在校全日制大学生，按院系和年级分层招募并记录基线学习水平",
        inclusion_criteria=[
            "年满18岁且当前为全日制在校大学生",
            "拥有可连续佩戴的研究级或经验证腕式活动记录设备，并能完成基线测验",
        ],
        exclusion_criteria=[
            "已确诊但未稳定治疗的睡眠障碍、严重精神或神经系统疾病",
            "轮班、跨时区旅行或正在使用显著影响睡眠/认知且近期调整剂量的药物",
        ],
        independent_variables=[
            "随机分组：睡眠延长与规律作息指导组 vs 维持原有作息的对照组",
            "腕式活动记录设备测得的每晚总睡眠时长（小时）及睡眠规律性",
        ],
        dependent_variables=[
            "主要结局：同一题库按预注册规则生成的每周标准化课程测验得分",
            "次要结局：心理运动警觉任务（PVT）的反应时中位数和遗漏次数",
        ],
        controls=[
            "两组在相同时间窗、相同环境和相同设备上完成测验",
            "题目难度、评分脚本、提醒频率与数据采集版本保持一致",
            "分析时控制基线测验得分、院系和年级，并固定随机种子与软件版本",
        ],
        confounders=[
            "基线GPA或等价的既往学业表现",
            "咖啡因与酒精摄入、心理压力和身体活动",
            "昼夜节律类型、课程负荷以及工作日/周末差异",
        ],
        intervention_or_exposure=(
            "先进行1周基线监测；干预组随后接受以增加卧床机会和固定起床时间为核心的3周睡眠延长指导，"
            "不实施睡眠剥夺；两组全程用腕式设备和每日简短日志记录依从性。"
        ),
        comparator="对照组维持原有作息，仅接受相同频率的数据采集提醒，并在研究结束后获得睡眠卫生材料",
        duration="每名参与者4周：1周基线监测加3周干预/对照观察",
        sample_size_plan=(
            "招募前根据主要结局的组别×时间交互效应进行功效分析：双侧α=0.05、功效至少80%，"
            "效应量和组内相关系数取自最相近的先验研究或盲态预实验，并预留失访比例；将全部输入、软件和最终样本量写入预注册。"
        ),
        allocation="由独立脚本按院系和基线测验水平分层、区组随机分配，分配序列在基线完成前隐藏",
        blinding="参与者无法对行为干预设盲；测验评分脚本、数据清洗人员和主要分析人员使用匿名组别编码",
        data_collection=[
            "腕式活动记录设备逐夜记录总睡眠时长、入睡/起床时间和睡眠效率，并保留原始时间戳",
            "每周在固定时段完成等值课程测验和PVT，记录设备、浏览器版本与完成时间",
            "每日记录咖啡因、酒精、运动、压力和异常事件，基线收集GPA、年级、专业及昼夜节律量表",
        ],
        analysis_plan=[
            "按意向治疗原则，以线性混合效应模型估计组别×时间交互项，参与者设随机截距",
            "报告平均差、标准化效应量、95%置信区间和精确P值，并检查残差与模型假设",
            "按预注册顺序进行依方案敏感性分析和多重结局校正，不依据显著性事后更换主要结局",
        ],
        missing_data_plan=(
            "记录每次缺失原因；主要分析在缺失随机假设下使用混合模型，缺失超过预注册阈值时增加多重插补和完整案例敏感性分析。"
        ),
        reproducibility=[
            "在公开时间戳平台预注册假设、主要结局、排除规则、功效分析和统计模型",
            "保存原始只读数据、数据字典、设备导出版本及逐步清洗日志",
            "以版本控制保存分析代码、依赖锁定文件、容器/环境说明和固定随机种子",
            "发布去标识化数据或受控访问说明，并用全新环境从原始数据重跑最终表格",
        ],
        ethics=[
            "实施前取得机构伦理审批和书面知情同意，允许参与者随时退出且不影响学业权益",
            "不要求睡眠剥夺；出现显著嗜睡、情绪恶化或其他风险信号时停止干预并转介专业评估",
        ],
        steps=[
            "冻结研究方案、主要结局和统计分析计划并完成伦理审批与预注册",
            "完成设备校准、测验等值性检查、评分脚本测试和研究人员培训",
            "按统一标准招募并完成知情同意、筛查和1周基线测量",
            "运行可复核的分层区组随机脚本并隐藏分配序列",
            "实施3周干预/对照流程，自动监测采集完整性但不查看组间结局",
            "按预注册规则锁库、去标识化、执行主分析和敏感性分析",
            "由独立复核者从原始数据重跑结果，并报告偏离方案、失访和不良事件",
        ],
        assumptions=_default_assumptions(request),
        constraints=constraints,
        requires_human_approval=True,
        generator="deterministic-domain-template-v2",
    )


def _extract_relation(question: str) -> tuple[str, str]:
    compact = re.sub(r"[？?。.]$", "", question.strip())
    patterns = (
        r"(?:研究|评估|检验)?\s*(.+?)\s*(?:是否|会不会|能否)?\s*(?:影响|改善|提高|降低|导致)\s*(.+?)(?:，|,|并设计|并制定|$)",
        r"(?:研究|评估|检验)?\s*(.+?)\s*与\s*(.+?)\s*之间\s*是否.*?(?:关系|关联)",
        r"(?:研究|评估|检验)?\s*(.+?)\s*与\s*(.+?)\s*(?:之间)?(?:是否)?(?:存在)?(?:关系|关联)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            exposure = match.group(1).strip(" ，,:：")
            outcome = match.group(2).strip(" ，,:：")
            if len(exposure) >= 2 and len(outcome) >= 2:
                return exposure, outcome
    return "预注册的主要暴露评分", "预注册的主要结局评分"


def _generic_plan(request: ResearchRequest) -> ExperimentPlan:
    exposure, outcome = _extract_relation(request.question)
    intent = build_search_intent(request.question, request.literature_query or "")
    population_terms = next(
        (
            dimension.query_terms
            for dimension in intent.dimensions
            if dimension.name == "population"
        ),
        (),
    )
    population = population_terms[0] if population_terms else "符合预注册纳入标准的目标成年人群"
    return ExperimentPlan(
        design_type="预注册的前瞻性重复测量对照研究",
        causal_scope="该默认方案优先估计时间顺序明确的关联；只有在干预可安全随机化时才解释为因果效应",
        hypothesis=f"{exposure}的预注册测量值变化与{outcome}的预注册测量值变化存在方向明确、可重复检验的关联。",
        population=f"{population}；从多个班级或场所连续招募以降低单一来源偏倚",
        inclusion_criteria=["满足研究问题界定的目标人群且能够提供知情同意", "能够完成全部基线测量和至少一次随访测量"],
        exclusion_criteria=["存在会使参与或测量不安全的急性健康状况", "基线时无法取得主要暴露或主要结局的有效测量"],
        independent_variables=[f"主要暴露：{exposure}，使用预注册量表、设备或日志按固定时间窗重复量化"],
        dependent_variables=[f"主要结局：{outcome}，使用同一版本的标准化任务、量表或客观记录量化"],
        controls=["所有测量使用相同操作手册、设备设置和采集时间窗", "分析控制基线结局和预注册人口学变量", "固定清洗规则、软件版本和随机种子"],
        confounders=["基线结局水平", "年龄、性别及研究问题相关的人口学因素", "时间趋势、环境变化和同期行为因素"],
        intervention_or_exposure=f"先完成基线测量，再在不少于三个预注册时间点重复记录{exposure}及依从性；若无法安全随机化，则不主动改变暴露。",
        comparator="按预注册阈值或安全随机化分组形成对照，并在分析前冻结分组规则",
        duration="包含基线期和至少三个等间隔随访时间点；具体日历在招募前写入预注册",
        sample_size_plan="根据主要结局、主要模型和最小重要效应进行先验功效分析，采用双侧α=0.05和至少80%功效，加入失访膨胀后冻结样本量。",
        allocation="若可安全干预则由独立脚本分层区组随机；观察性设计按预注册暴露定义分组并使用倾向评分或协变量调整",
        blinding="不能对参与者设盲时，对结局评分者、数据清洗人员和主要分析人员隐藏组别编码",
        data_collection=[f"按固定时间窗记录{exposure}的原始值、时间戳、设备或量表版本", f"按相同时间表记录{outcome}并保留评分脚本输出", "记录依从性、缺失原因、异常事件和预注册混杂因素"],
        analysis_plan=["使用包含参与者随机截距的混合效应模型估计暴露、时间及其交互项", "报告效应量、95%置信区间和模型诊断，不只报告显著性", "执行预注册的稳健性、缺失数据和替代定义敏感性分析"],
        missing_data_plan="逐项记录缺失原因；主分析使用能处理不平衡重复测量的模型，并以多重插补和完整案例分析检验稳健性。",
        reproducibility=["预注册假设、主要结局、排除规则、样本量和统计模型", "保留原始只读数据、数据字典和逐步清洗日志", "版本控制代码并锁定依赖、软件版本和随机种子", "由独立人员在全新环境重跑主结果"],
        ethics=["实施前取得适用的伦理审批和知情同意", "采用最小风险原则、数据最小化和去标识化存储"],
        steps=["完成伦理审查与预注册", "试运行测量工具并冻结操作手册", "按统一标准招募和基线测量", "执行分配或暴露分类并开展重复测量", "按预注册规则锁库和分析", "独立复核代码、偏离方案和最终结果"],
        assumptions=_default_assumptions(request),
        constraints=list(request.constraints),
        requires_human_approval=True,
        generator="deterministic-generic-protocol-v2",
    )


def deterministic_experiment_plan(request: ResearchRequest) -> ExperimentPlan:
    source = f"{request.question} {request.objective}".casefold()
    sleep_terms = ("睡眠", "sleep", "午睡", "nap")
    academic_terms = ("学习表现", "学业表现", "学习成绩", "academic performance", "gpa")
    if any(term in source for term in sleep_terms) and any(
        term in source for term in academic_terms
    ):
        return _sleep_academic_plan(request)
    return _generic_plan(request)


async def _llm_experiment_plan(
    request: ResearchRequest,
    client: CompletionClient,
) -> ExperimentPlan:
    schema_fields = ", ".join(ExperimentPlan.model_fields)
    response = await client.complete(
        LLMCompletionRequest(
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "You are a research-methods protocol designer. Return exactly one JSON object "
                        "for a concrete, ethical, reproducible study. Never use placeholders such as "
                        "'to be determined', 'appropriate measure', 'research condition', '视情况', "
                        "'待定', '研究条件', or '与研究目标一致'. Operationalize every variable with a "
                        "measurement, unit/score, timing, or instrument. Do not invent study results or "
                        "citations. If causal manipulation is unsafe, select a prospective observational "
                        "design and state the causal limitation. The JSON keys must be exactly: "
                        f"{schema_fields}. All plural fields are arrays of strings. Include at least 2 "
                        "inclusion and exclusion criteria, 3 controls and confounders, 3 data collection "
                        "and analysis items, 4 reproducibility items, 2 ethics items, and 6 ordered steps. "
                        "sample_size_plan must specify alpha, power, effect-size source, attrition, and "
                        "freezing the final sample before recruitment. generator must be 'llm-protocol-v2'."
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=request.model_dump_json(exclude={"documents"})[:20_000],
                ),
            ],
            temperature=0,
            max_tokens=2600,
            response_format="json_object",
        )
    )
    payload = extract_json_object(response.content)
    if payload is None:
        raise ValueError("LLM experiment planner returned no JSON object")
    payload["constraints"] = list(request.constraints)
    payload["requires_human_approval"] = True
    payload["generator"] = "llm-protocol-v2"
    return validate_experiment_plan(payload)


async def generate_experiment_plan(
    request: ResearchRequest,
    *,
    llm_client: CompletionClient | None = None,
) -> dict[str, Any]:
    provider_enabled = os.getenv("RESEARCH_MESH_LLM_PROVIDER", "disabled").strip().lower() != "disabled"
    use_llm = llm_client is not None or _env_bool(
        "RESEARCH_MESH_EXPERIMENT_USE_LLM", provider_enabled
    )
    if use_llm:
        client = llm_client or LLMGatewayClient.from_environment()
        try:
            plan = await _llm_experiment_plan(request, client)
            return plan.model_dump(mode="json")
        except (OSError, ValueError, ValidationError, RuntimeError):
            # A methods service must remain useful if the optional model is unavailable
            # or returns an invalid protocol; the deterministic plan is fully validated.
            pass
    return deterministic_experiment_plan(request).model_dump(mode="json")
