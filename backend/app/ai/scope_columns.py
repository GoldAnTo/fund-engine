"""Fixed JSON paths shared by audit queries and expression indexes.

SQLite cannot match an expression index when the JSON path is a bound
parameter. These internal, fixed paths compile literally on both dialects.
"""
from sqlalchemy import String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement


class AuditCaseRef(FunctionElement):
    type = String()
    inherit_cache = True
    key = 'research_case_id'


class AuditRunRef(FunctionElement):
    type = String()
    inherit_cache = True
    key = 'research_run_id'


@compiles(AuditCaseRef, 'sqlite')
@compiles(AuditRunRef, 'sqlite')
def _sqlite(element, compiler, **kw):
    column = compiler.process(list(element.clauses)[0], **kw)
    return f'''JSON_EXTRACT({column}, '$."{element.key}"')'''


@compiles(AuditCaseRef)
@compiles(AuditRunRef)
def _postgres(element, compiler, **kw):
    column = compiler.process(list(element.clauses)[0], **kw)
    return f"CAST({column} ->> '{element.key}' AS VARCHAR)"
