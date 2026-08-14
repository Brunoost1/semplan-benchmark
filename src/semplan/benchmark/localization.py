"""Locale-specific surface forms for benchmark utterance generation."""

from __future__ import annotations

from semplan.contracts import Locale

METRIC_SURFACE: dict[str, dict[Locale, str]] = {
    "gross_revenue": {Locale.EN_US: "gross revenue", Locale.PT_BR: "receita bruta"},
    "net_revenue": {Locale.EN_US: "net revenue", Locale.PT_BR: "receita líquida"},
    "contribution_margin": {
        Locale.EN_US: "contribution margin",
        Locale.PT_BR: "margem de contribuição",
    },
    "contribution_margin_pct": {
        Locale.EN_US: "contribution margin percent",
        Locale.PT_BR: "percentual da margem de contribuição",
    },
    "order_count": {Locale.EN_US: "order count", Locale.PT_BR: "quantidade de pedidos"},
    "average_order_value": {
        Locale.EN_US: "average order value",
        Locale.PT_BR: "valor médio do pedido",
    },
    "expense_amount": {Locale.EN_US: "expense amount", Locale.PT_BR: "valor das despesas"},
    "budget_amount": {Locale.EN_US: "budget amount", Locale.PT_BR: "valor orçado"},
    "budget_variance": {
        Locale.EN_US: "budget variance",
        Locale.PT_BR: "variação orçamentária",
    },
    "budget_variance_pct": {
        Locale.EN_US: "budget variance percent",
        Locale.PT_BR: "percentual de variação orçamentária",
    },
    "active_contract_value": {
        Locale.EN_US: "active contract value",
        Locale.PT_BR: "valor de contratos ativos",
    },
    "active_customer_count": {
        Locale.EN_US: "active customer count",
        Locale.PT_BR: "quantidade de clientes ativos",
    },
}

DIMENSION_SURFACE: dict[str, dict[Locale, str]] = {
    "date": {Locale.EN_US: "date", Locale.PT_BR: "data"},
    "year": {Locale.EN_US: "year", Locale.PT_BR: "ano"},
    "quarter": {Locale.EN_US: "quarter", Locale.PT_BR: "trimestre"},
    "month": {Locale.EN_US: "month", Locale.PT_BR: "mês"},
    "week": {Locale.EN_US: "week", Locale.PT_BR: "semana"},
    "region": {Locale.EN_US: "region", Locale.PT_BR: "região"},
    "country": {Locale.EN_US: "country", Locale.PT_BR: "país"},
    "customer_segment": {
        Locale.EN_US: "customer segment",
        Locale.PT_BR: "segmento de clientes",
    },
    "channel": {Locale.EN_US: "channel", Locale.PT_BR: "canal"},
    "product": {Locale.EN_US: "product", Locale.PT_BR: "produto"},
    "category": {Locale.EN_US: "category", Locale.PT_BR: "categoria"},
    "subcategory": {Locale.EN_US: "subcategory", Locale.PT_BR: "subcategoria"},
    "brand": {Locale.EN_US: "brand", Locale.PT_BR: "marca"},
    "department": {Locale.EN_US: "department", Locale.PT_BR: "departamento"},
    "cost_center": {Locale.EN_US: "cost center", Locale.PT_BR: "centro de custo"},
    "expense_category": {
        Locale.EN_US: "expense category",
        Locale.PT_BR: "categoria de despesa",
    },
    "supplier": {Locale.EN_US: "supplier", Locale.PT_BR: "fornecedor"},
    "contract_risk": {Locale.EN_US: "contract risk", Locale.PT_BR: "risco contratual"},
    "payment_method": {Locale.EN_US: "payment method", Locale.PT_BR: "forma de pagamento"},
}

PT_METRIC_ARTICLES = {
    "gross_revenue": "a",
    "net_revenue": "a",
    "contribution_margin": "a",
    "contribution_margin_pct": "o",
    "order_count": "a",
    "average_order_value": "o",
    "expense_amount": "o",
    "budget_amount": "o",
    "budget_variance": "a",
    "budget_variance_pct": "o",
    "active_contract_value": "o",
    "active_customer_count": "a",
}

PT_DIMENSION_PLURALS: dict[str, tuple[str, str]] = {
    "region": ("as", "regiões"),
    "channel": ("os", "canais"),
    "category": ("as", "categorias"),
    "customer_segment": ("os", "segmentos de clientes"),
    "payment_method": ("as", "formas de pagamento"),
    "department": ("os", "departamentos"),
    "cost_center": ("os", "centros de custo"),
    "expense_category": ("as", "categorias de despesa"),
    "contract_risk": ("os", "níveis de risco contratual"),
    "month": ("os", "meses"),
    "quarter": ("os", "trimestres"),
}

EN_DIMENSION_PLURALS: dict[str, str] = {
    "region": "regions",
    "channel": "channels",
    "category": "categories",
    "customer_segment": "customer segments",
    "payment_method": "payment methods",
    "department": "departments",
    "cost_center": "cost centers",
    "expense_category": "expense categories",
    "contract_risk": "contract risk levels",
    "month": "months",
    "quarter": "quarters",
}

REGION_SURFACE = {
    "North": {Locale.EN_US: "North", Locale.PT_BR: "Norte"},
    "South": {Locale.EN_US: "South", Locale.PT_BR: "Sul"},
    "East": {Locale.EN_US: "East", Locale.PT_BR: "Leste"},
    "West": {Locale.EN_US: "West", Locale.PT_BR: "Oeste"},
    "Central": {Locale.EN_US: "Central", Locale.PT_BR: "Central"},
}

SEGMENT_SURFACE = {
    "consumer": {Locale.EN_US: "consumer", Locale.PT_BR: "consumidor"},
    "small_business": {Locale.EN_US: "small business", Locale.PT_BR: "pequenas empresas"},
    "mid_market": {Locale.EN_US: "mid-market", Locale.PT_BR: "mercado intermediário"},
    "enterprise": {Locale.EN_US: "enterprise", Locale.PT_BR: "empresarial"},
    "education": {Locale.EN_US: "education", Locale.PT_BR: "educação"},
    "public": {Locale.EN_US: "public sector", Locale.PT_BR: "setor público"},
}

CHANNEL_SURFACE = {
    "online": {Locale.EN_US: "online", Locale.PT_BR: "online"},
    "marketplace": {Locale.EN_US: "marketplace", Locale.PT_BR: "marketplace"},
    "retail": {Locale.EN_US: "retail", Locale.PT_BR: "varejo"},
    "wholesale": {Locale.EN_US: "wholesale", Locale.PT_BR: "atacado"},
    "partner": {Locale.EN_US: "partner", Locale.PT_BR: "parceiros"},
    "mobile": {Locale.EN_US: "mobile", Locale.PT_BR: "mobile"},
}

PAYMENT_METHOD_SURFACE = {
    "card": {Locale.EN_US: "card", Locale.PT_BR: "cartão"},
    "debit": {Locale.EN_US: "debit", Locale.PT_BR: "débito"},
    "wallet": {Locale.EN_US: "wallet", Locale.PT_BR: "carteira digital"},
    "bank_transfer": {
        Locale.EN_US: "bank transfer",
        Locale.PT_BR: "transferência bancária",
    },
    "invoice": {Locale.EN_US: "invoice", Locale.PT_BR: "fatura"},
    "voucher": {Locale.EN_US: "voucher", Locale.PT_BR: "voucher"},
}

RISK_SURFACE = {
    "low": {Locale.EN_US: "low", Locale.PT_BR: "baixo"},
    "medium": {Locale.EN_US: "medium", Locale.PT_BR: "médio"},
    "high": {Locale.EN_US: "high", Locale.PT_BR: "alto"},
    "critical": {Locale.EN_US: "critical", Locale.PT_BR: "crítico"},
}

MONTH_NAMES = {
    Locale.EN_US: (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    Locale.PT_BR: (
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
}


def metric_surface(metric_id: str, locale: Locale) -> str:
    return METRIC_SURFACE.get(metric_id, {}).get(locale, metric_id.replace("_", " "))


def dimension_surface(dimension_id: str, locale: Locale) -> str:
    return DIMENSION_SURFACE.get(dimension_id, {}).get(locale, dimension_id.replace("_", " "))


def metric_noun_phrase_pt(metric_id: str) -> str:
    return f"{PT_METRIC_ARTICLES.get(metric_id, 'o')} {metric_surface(metric_id, Locale.PT_BR)}"


def metric_de_phrase_pt(metric_id: str) -> str:
    article = PT_METRIC_ARTICLES.get(metric_id, "o")
    contraction = "da" if article == "a" else "do"
    return f"{contraction} {metric_surface(metric_id, Locale.PT_BR)}"


def dimension_plural(dimension_id: str, locale: Locale) -> str:
    if locale is Locale.PT_BR:
        return PT_DIMENSION_PLURALS.get(
            dimension_id,
            ("os", f"{dimension_surface(dimension_id, locale)}s"),
        )[1]
    return EN_DIMENSION_PLURALS.get(dimension_id, f"{dimension_surface(dimension_id, locale)}s")


def dimension_plural_article_pt(dimension_id: str) -> str:
    return PT_DIMENSION_PLURALS.get(dimension_id, ("os", ""))[0]


def value_surface(field: str, value: object, locale: Locale) -> str:
    if field == "region" and isinstance(value, str):
        return REGION_SURFACE.get(value, {}).get(locale, value)
    if field == "customer_segment" and isinstance(value, str):
        return SEGMENT_SURFACE.get(value, {}).get(locale, value.replace("_", " "))
    if field == "channel" and isinstance(value, str):
        return CHANNEL_SURFACE.get(value, {}).get(locale, value.replace("_", " "))
    if field == "payment_method" and isinstance(value, str):
        return PAYMENT_METHOD_SURFACE.get(value, {}).get(locale, value.replace("_", " "))
    if field == "contract_risk" and isinstance(value, str):
        return RISK_SURFACE.get(value, {}).get(locale, value.replace("_", " "))
    if field == "department" and isinstance(value, str) and value.startswith("department_"):
        return f"departamento {value.rsplit('_', 1)[1]}" if locale is Locale.PT_BR else value
    if field == "expense_category" and isinstance(value, str) and value.startswith("expense_"):
        return (
            f"categoria de despesa {value.rsplit('_', 1)[1]}" if locale is Locale.PT_BR else value
        )
    if field == "category" and isinstance(value, str) and value.startswith("category_"):
        return f"categoria {value.rsplit('_', 1)[1]}" if locale is Locale.PT_BR else value
    return str(value).replace("_", " ") if locale is Locale.EN_US else str(value)


def month_name(month: int, locale: Locale) -> str:
    return MONTH_NAMES[locale][month - 1]


def month_period(month: int, year: int, locale: Locale) -> str:
    if locale is Locale.PT_BR:
        return f"{month_name(month, locale)} de {year}"
    return f"{month_name(month, locale)} {year}"


def quarter_period(quarter: int, year: int, locale: Locale) -> str:
    if locale is Locale.PT_BR:
        return f"{quarter}º trimestre de {year}"
    return f"Q{quarter} {year}"
