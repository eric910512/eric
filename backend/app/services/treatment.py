"""Treatment-cycle helpers shared by the dashboard and observation endpoints."""

from datetime import timedelta

from app.models.enums import CycleStatus, PlanStatus


def active_plan_and_cycle(patient):
    """The patient's active (or planned) chemotherapy plan and its in-progress cycle."""
    plans = [p for p in patient.chemotherapy_plans if p.deleted_at is None]
    plans.sort(key=lambda p: (p.status != PlanStatus.ACTIVE, p.status != PlanStatus.PLANNED, -p.start_date.toordinal()))
    plan = plans[0] if plans and plans[0].status in (PlanStatus.ACTIVE, PlanStatus.PLANNED) else None
    cycle = None
    if plan:
        cycle = next(
            (c for c in plan.cycles if c.deleted_at is None and c.status == CycleStatus.IN_PROGRESS), None
        )
    return plan, cycle


def cycle_nadir(cycle, day):
    """(in_nadir, nadir_start_date, nadir_end_date) of a started cycle on local date ``day``."""
    if cycle is None or cycle.actual_start_date is None or cycle.nadir_start_day is None:
        return False, None, None
    start = cycle.actual_start_date + timedelta(days=cycle.nadir_start_day - 1)
    end = cycle.actual_start_date + timedelta(days=(cycle.nadir_end_day or cycle.nadir_start_day) - 1)
    return start <= day <= end, start, end
