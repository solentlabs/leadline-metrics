"""The GitHub sources: a platform group, with what they share: the API, the token, a repo's check and file-name
fields, pages, GraphQL, times.

The code is in api.py (HTTP); spec.py holds the pure checks of an entry (a source's spec.py imports those). This
__init__ imports neither, so a source's spec loads without the API; `lazy` finds the names below on first use
(docs/ARCHITECTURE.md, "Dependency rules").
"""

from typing import TYPE_CHECKING

from leadline.sources import lazy

if TYPE_CHECKING:  # for type checkers only
    from leadline.sources.github.api import (
        API,
        CREDENTIAL,
        GRAPHQL,
        GRAPHQL_REASONS,
        GRAPHQL_UNFIXABLE,
        MAINTAINER_FIELD,
        MAINTAINER_KIND,
        MISSING,
        NO_GRAPHQL,
        NO_PUSH_ACCESS,
        READABLE,
        GraphqlError,
        check_access,
        check_maintainer,
        check_query,
        check_repo,
        check_traffic,
        failed_finding,
        graphql,
        headers,
        local_date,
        numbered_pages,
        owner_and_name,
        parse_time,
    )
    from leadline.sources.github.spec import file_fields, maintainer_problems, repo_problems

GROUP = True

__getattr__ = lazy(__name__, ("spec", "api"))

__all__ = [
    "API",
    "CREDENTIAL",
    "GRAPHQL",
    "GRAPHQL_REASONS",
    "GRAPHQL_UNFIXABLE",
    "MAINTAINER_FIELD",
    "MAINTAINER_KIND",
    "MISSING",
    "NO_GRAPHQL",
    "NO_PUSH_ACCESS",
    "READABLE",
    "GraphqlError",
    "check_access",
    "check_maintainer",
    "check_query",
    "check_repo",
    "check_traffic",
    "failed_finding",
    "file_fields",
    "graphql",
    "headers",
    "local_date",
    "maintainer_problems",
    "numbered_pages",
    "owner_and_name",
    "parse_time",
    "repo_problems",
]
