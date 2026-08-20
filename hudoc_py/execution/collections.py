"""Public collection constants for the HUDOC-EXEC search API."""

from __future__ import annotations

# HUDOC-EXEC uses a different ranking model GUID than HUDOC main.
EXEC_RANKING_MODEL_ID = "44444444-b0a6-44c9-bb6b-5886b928f985"

# Compound expression spanning EXEC and ECHR content sites – the literal query
# the HUDOC-EXEC frontend sends.
EXEC_BASE_QUERY = (
    "((contentsitename:EXEC) OR "
    "(contentsitename:ECHR AND (execdocumenttype:EXECUTION OR execdocumenttype:MERITS)))"
)

# Document type taxonomy. Keys are codes passed to ``execdocumenttypecollection``;
# values are human-readable labels.
COLLECTION_CODES: dict[str, str] = {
    # Top-level
    "CEC": "Cases",
    "acp": "Action Plans",
    "acr": "Action Reports",
    # Communications (parent: obs)
    "obs": "Communications (all)",
    "apo": "Comm: Applicant",
    "gvo": "Comm: Government",
    "eo": "Comm: ECHR",
    "ngo": "Comm: NGO",
    "nhri": "Comm: NHRI",
    "igo": "Comm: IGO",
    "nto": "Comm: UNHCR",
    "oorg": "Comm: Other Organisations",
    "oo": "Comm: Other",
    # CM Documents
    "CMDEC": "CM Decisions",
    "CMINF": "CM Info",
    "CMNOT": "CM Notes",
    "HEXEC": "H/Exec Memos",
    # Resolutions (parent: res)
    "res": "Resolutions (all)",
    "EXECUTION": "Res: Execution of Court Judgments",
    "MERITS": "Res: Execution of CM Decisions (former Art. 32)",
}


def collection_code(value: str | None) -> str:
    """Return the first known code from HUDOC-EXEC's semicolon field.

    Live records commonly expose values such as ``CEC;all`` and ``acp;all``;
    callers should not compare the raw compound value directly.
    """
    for candidate in str(value or "").split(";"):
        code = candidate.strip()
        if code in COLLECTION_CODES:
            return code
    return str(value or "").split(";", 1)[0].strip()


# Comprehensive select for cases – all exec-prefixed fields.
CASE_SELECT_FIELDS = (
    "sharepointid,rank,contentsitename,execranking,statesortordereng,"
    "execidentifier,execdocumenttype,exectitle,execlanguage,execstate,"
    "execprecedentcases,execisprecedent,execappno,execcontentstoretype,"
    "execcontentstoreid,execcmmeetingnumber,execsupervision,execviolations,"
    "execviolationsfromechr,execshortdesc,execfs,execfswithut,exectype,"
    "execresolutionnumber,execisclosed,execthemedomain,execapstatus,"
    "execdocumentreference,execitemidfromechr,execdocnamefromechr,"
    "execprecedentappnos,execpublisheddateastext,execjudgmentdateastext,"
    "execfinaljudgmentdateastext,execpublisheddate,execjudgmentdate,"
    "execfinaljudgmentdate,execshortstatusexecution,execpaymentstatus,"
    "execpaymentdateastext,execpaymentdate,isplaceholder,execclassindicator,"
    "execfinalresolutiondate,execfinalresolutiondateastext,execmastergroupid,"
    "execdocumenttypecollection"
)

# Smaller select for non-case documents (action plans, CM decisions, etc.).
DOC_SELECT_FIELDS = (
    "sharepointid,rank,contentsitename,execidentifier,execdocumenttype,"
    "exectitle,execlanguage,execstate,execappno,execcontentstoretype,"
    "execcontentstoreid,execcmmeetingnumber,execdocumentreference,"
    "execpublisheddate,execpublisheddateastext,execdocumenttypecollection,"
    "execprecedentappnos,execitemidfromechr,isplaceholder"
)
