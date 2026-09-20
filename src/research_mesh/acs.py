from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .config import RuntimeSettings


@dataclass(frozen=True)
class AgentCardDefinition:
    slug: str
    name: str
    description: str
    skill_id: str
    skill_name: str
    skill_description: str
    tags: tuple[str, ...]
    examples: tuple[str, ...]


AGENT_CARDS: dict[str, AgentCardDefinition] = {
    "leader": AgentCardDefinition(
        slug="leader",
        name="基于多智能体协作的一站式科研助理平台",
        description=(
            "面向科研问题的协作编排智能体。按需发现并调用文献检索、实验设计、"
            "数据分析和规范复核 Partner，汇总可追溯结果；不代替人工完成伦理审批或"
            "高风险研究决策。"
        ),
        skill_id="research-collaboration.orchestration",
        skill_name="多智能体科研协作编排",
        skill_description=(
            "接收研究问题、目标、数据与约束，动态组织四类科研 Partner，执行并行任务、"
            "规范复核和来源追踪，输出结构化研究报告。"
        ),
        tags=("科研协作", "多智能体", "任务编排", "可追溯", "规范复核"),
        examples=(
            "围绕睡眠时长与学习表现设计一项研究，并分析这组样本数据",
            "检索相关文献，给出可复现的实验方案和统计摘要",
            "对这份研究问题、实验设计和数据结论做完整规范复核",
        ),
    ),
    "literature": AgentCardDefinition(
        slug="literature",
        name="文献证据智能体",
        description=(
            "通过 Crossref 检索公开文献元数据，返回题名、作者、年份、来源与 DOI；"
            "不声称阅读全文，不伪造无法验证的引文或结论。"
        ),
        skill_id="literature-search",
        skill_name="可追溯文献检索",
        skill_description="按研究问题检索公开文献元数据，并保留 DOI 与检索来源。",
        tags=("科研协作", "文献", "Crossref", "DOI", "证据"),
        examples=(
            "检索睡眠时长与大学生学业表现相关的五篇文献",
            "为这个研究假设查找包含 DOI 的可追溯证据",
            "根据关键词返回公开文献元数据，不要生成虚构引用",
        ),
    ),
    "experiment": AgentCardDefinition(
        slug="experiment",
        name="实验设计智能体",
        description=(
            "把研究问题转换为结构化假设、变量、控制条件和实验步骤，并显式标记需要"
            "人工审批的环节；不代替伦理委员会或领域专家作最终决策。"
        ),
        skill_id="experiment-design",
        skill_name="结构化实验设计",
        skill_description="依据研究目标、数据指标和约束生成可审查的实验设计草案。",
        tags=("科研协作", "实验设计", "假设", "变量", "控制条件"),
        examples=(
            "为睡眠与学习表现的关系设计观察性研究",
            "根据目标指标列出自变量、因变量和控制条件",
            "生成包含人工审批节点的可复现研究步骤",
        ),
    ),
    "analysis": AgentCardDefinition(
        slug="analysis",
        name="数据分析智能体",
        description=(
            "对调用方提供的有限数值数据执行确定性的描述性统计和有限性检查；"
            "不进行因果推断，也不把小样本统计结果外推到总体。"
        ),
        skill_id="data-analysis",
        skill_name="可复现描述性统计",
        skill_description="计算样本量、均值、中位数、极值和样本标准差并记录分析引擎。",
        tags=("科研协作", "数据分析", "描述性统计", "可复现"),
        examples=(
            "分析这组学习成绩并返回均值、中位数和样本标准差",
            "检查输入数值是否有限并生成可复现统计摘要",
            "只做描述性统计，不进行因果推断",
        ),
    ),
    "review": AgentCardDefinition(
        slug="review",
        name="规范复核智能体",
        description=(
            "独立检查科研协作产物中的文献证据、实验控制、样本限制与约束登记，"
            "输出错误、警告和审查决定。"
        ),
        skill_id="method-review",
        skill_name="方法与证据规范复核",
        skill_description="对其他 Partner 的产物执行一致性与最低规范检查。",
        tags=("科研协作", "规范检查", "证据核验", "方法复核"),
        examples=(
            "检查研究包是否包含可追溯文献和实验控制条件",
            "对小样本结论给出外推风险警告",
            "审查研究约束是否登记完整并给出接受或修订决定",
        ),
    ),
}


def _certificate_alt_names(endpoint: str) -> dict[str, list[str]]:
    host = urlparse(endpoint).hostname
    if not host:
        return {}
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return {"dns": [host]}
    return {"ip": [host]}


def build_acs(
    slug: str,
    *,
    settings: RuntimeSettings,
    endpoint: str,
    registration_template: bool = False,
) -> dict[str, Any]:
    """Build an ACS v02.02 document for runtime display or Registry submission."""

    definition = AGENT_CARDS[slug]
    platform_security = settings.mode == "platform" or registration_template
    provider: dict[str, Any] = {
        "countryCode": os.getenv("RESEARCH_MESH_PROVIDER_COUNTRY_CODE", "CN"),
        "organization": os.getenv(
            "RESEARCH_MESH_PROVIDER_ORGANIZATION", "参赛团队待填写"
        ),
    }
    optional_provider_fields = {
        "department": os.getenv("RESEARCH_MESH_PROVIDER_DEPARTMENT", "").strip(),
        "url": os.getenv("RESEARCH_MESH_PROVIDER_URL", "").strip(),
        "license": os.getenv("RESEARCH_MESH_PROVIDER_LICENSE", "").strip(),
    }
    provider.update(
        {key: value for key, value in optional_provider_fields.items() if value}
    )
    contact_name = os.getenv("RESEARCH_MESH_PROVIDER_CONTACT_NAME", "").strip()
    contact_email = os.getenv("RESEARCH_MESH_PROVIDER_CONTACT_EMAIL", "").strip()
    domain = os.getenv("RESEARCH_MESH_PROVIDER_DOMAIN", "").strip()
    domain_registration = os.getenv(
        "RESEARCH_MESH_PROVIDER_DOMAIN_REGISTRATION", ""
    ).strip()
    domain_registration_type = os.getenv(
        "RESEARCH_MESH_PROVIDER_DOMAIN_REGISTRATION_TYPE", "ICP"
    ).strip()
    if contact_name:
        provider["name"] = contact_name
    if contact_email:
        provider["email"] = contact_email
    if domain and domain_registration:
        provider["domainRegistrations"] = [
            {
                "domain": domain,
                "registrationNumber": domain_registration,
                "registrationType": domain_registration_type,
            }
        ]

    configured_aic = settings.aic_for(slug)
    document: dict[str, Any] = {
        "aic": (
            ""
            if registration_template and configured_aic.startswith("local.")
            else configured_aic
        ),
        "active": not registration_template,
        "lastModifiedTime": datetime.now(
            timezone(timedelta(hours=8))
        ).isoformat(),
        "protocolVersion": "02.02",
        "name": definition.name,
        "description": definition.description,
        "version": "0.5.0",
        "provider": provider,
        "securitySchemes": (
            {
                "mtls": {
                    "type": "mutualTLS",
                    "description": "ACPs CA 签发证书的智能体间 mTLS 双向认证",
                }
            }
            if platform_security
            else {}
        ),
        "endPoints": [
            {
                "url": endpoint,
                "transport": "JSONRPC",
                **({"security": [{"mtls": []}]} if platform_security else {}),
            }
        ],
        "capabilities": {
            "streaming": False,
            "notification": False,
            "messageQueue": [],
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": definition.skill_id,
                "name": definition.skill_name,
                "description": definition.skill_description,
                "version": "0.5.0",
                "tags": list(definition.tags),
                "examples": list(definition.examples),
                "inputModes": ["application/json", "text/plain"],
                "outputModes": ["application/json"],
            }
        ],
    }
    if platform_security:
        alt_names = _certificate_alt_names(endpoint)
        if alt_names:
            document["certificate"] = {
                "altNames": alt_names,
                "requestedValidity": 365,
            }
    return document
