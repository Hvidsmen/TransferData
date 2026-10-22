"""Paginate whole groups so their jobs are never split across pages."""
from django.core.paginator import Paginator
from django.db.models import Count, Q


def group_jobs(records, page_number):
    summaries = list(records.order_by().values("group_id", "group__name").annotate(total=Count("pk")))
    summaries.sort(key=lambda row: (row["group_id"] is None, (row["group__name"] or "").casefold(), row["group_id"] or 0))
    page = Paginator(summaries, 6).get_page(page_number)
    sections = [{"id": row["group_id"], "name": row["group__name"] or "Без группы",
                 "count": row["total"], "jobs": []} for row in page]
    index = {section["id"]: section for section in sections}
    condition = Q(group_id__in=[key for key in index if key is not None])
    if None in index:
        condition |= Q(group__isnull=True)
    jobs = list(records.filter(condition).order_by("name", "pk")) if sections else []
    for job in jobs:
        index[job.group_id]["jobs"].append(job)
    return page, sections, jobs
