"""Arclane orchestrator for internal prompt execution or bridge fallback."""

import os
from copy import deepcopy
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.config import settings
from arclane.core.logging import get_logger
from arclane.engine.executive_prompts import ORCHESTRATOR_SYSTEM_PROMPT, phase_context_block, prompt_pack_for_area
from arclane.engine.intake import build_intake_brief, build_task_plan
from arclane.engine.llm_client import ArclaneLLMClient
from arclane.integrations.content_production_client import ContentProductionClient
from arclane.integrations.kh_publisher import KHPublisher
from arclane.integrations.nexus_publisher import NexusPublisher
from arclane.models.tables import Activity, Business, Content, Cycle, Metric
from arclane.notifications import send_working_days_low_email, send_cycle_complete_email, send_task_complete_email
from arclane.performance.pipeline_metrics import pipeline_metrics
from arclane.performance.webhook_cycles import cycle_webhook_notifier
from arclane.performance.websocket import ws_manager
from arclane.services.workflow_service import WorkflowService
from arclane.services.roadmap_service import (
    complete_milestone,
    check_phase_graduation,
    advance_phase,
    generate_phase_tasks,
    get_phase_for_day,
)
from arclane.services.advisory_service import generate_advisory_notes
from arclane.services.health_score_service import record_health_snapshot

log = get_logger("orchestrator")

# Map C-Suite agent names to user-friendly action labels
AGENT_ACTION_MAP = {
    "cos": "Planning next steps",
    "cto": "Building features",
    "cfo": "Analyzing finances",
    "cmo": "Creating content",
    "cio": "Reviewing security",
    "cpo": "Refining product",
    "cro": "Evaluating performance",
    "cdo": "Processing data",
    "cengo": "Engineering improvements",
    "cseco": "Scanning for vulnerabilities",
    "cco": "Crafting communications",
    "cso": "Researching market",
    "crevo": "Optimizing revenue",
    "crio": "Assessing risks",
    "ccomo": "Ensuring compliance",
    "coo": "Streamlining operations",
    "advertising": "Creating ad campaigns",
}


class ArclaneOrchestrator:
    """Execute business work through internal prompts or the legacy bridge."""

    def __init__(self, execution_mode: str | None = None, llm_client: ArclaneLLMClient | None = None):
        self._execution_mode = (execution_mode or settings.orchestration_mode or "internal").lower()
        self._csuite_url = settings.csuite_base_url
        self._service_token = settings.zuul_service_token
        self._workflow_service = WorkflowService()
        self._kh_publisher = KHPublisher()
        self._nexus_publisher = NexusPublisher()
        self._llm_client = llm_client or ArclaneLLMClient()
        # Content Production client for ebook delegation (None if not configured)
        cp_url = os.environ.get("CONTENT_PRODUCTION_URL", "")
        self._cp_client: ContentProductionClient | None = (
            ContentProductionClient(base_url=cp_url) if cp_url else None
        )

    async def execute_cycle(
        self, business: Business, cycle: Cycle, session: AsyncSession
    ) -> dict:
        """Execute a full autonomous cycle for a business."""
        log.info(
            "Executing cycle %d for business %s via %s mode",
            cycle.id, business.slug, self._execution_mode,
        )

        cycle.status = "running"
        cycle.started_at = datetime.now(timezone.utc)
        await session.commit()

        pipeline_metrics.record_cycle_start(cycle.trigger, business.plan)

        # Push real-time update to connected clients
        await ws_manager.broadcast_cycle_progress(
            business.id, cycle.id, "running", 0.0,
        )

        plan = cycle.plan or {}
        task_description = plan.get("task_description")
        tasks = (
            [{"area": "general", "action": "execute_task", "description": task_description}]
            if task_description
            else self._build_tasks(business, cycle_trigger=cycle.trigger)
        )
        # Handle async roadmap task generation
        if tasks and tasks[0].get("_roadmap_async"):
            phase = tasks[0].get("_phase", 0)
            if phase >= 5:
                from arclane.services.ongoing_optimizer import select_adaptive_task
                adaptive = await select_adaptive_task(business, session)
                tasks = [adaptive] if adaptive else []
            else:
                tasks = await generate_phase_tasks(business, session)
                # If no tasks for today's day, check if graduation criteria are met
                # and auto-advance to the next phase so the cycle isn't wasted.
                if not tasks and 1 <= (business.current_phase or 0) <= 4:
                    advance_result = await advance_phase(business, session)
                    if advance_result["advanced"]:
                        log.info(
                            "Auto-advanced %s from phase %d to %d (no tasks for day %d)",
                            business.slug,
                            advance_result["from_phase"],
                            advance_result["to_phase"],
                            business.roadmap_day or 0,
                        )
                        tasks = await generate_phase_tasks(business, session)
        cycle_label = self._cycle_queue_label(tasks)

        session.add(
            Activity(
                business_id=business.id,
                cycle_id=cycle.id,
                agent="system",
                action="Cycle started",
                detail=cycle_label,
            )
        )
        await session.commit()

        try:
            if self._execution_mode == "bridge":
                cycle_result = await self._execute_bridge_cycle(business, cycle, tasks)
            else:
                cycle_result = await self._execute_internal_cycle(business, cycle, tasks, session)
        except Exception as exc:
            log.error("Cycle %d failed for %s: %s", cycle.id, business.slug, exc, exc_info=True)
            return await self._mark_cycle_failed(business, cycle, session)

        for activity in self._sync_operating_plan_after_cycle(business, tasks, cycle_result):
            session.add(
                Activity(
                    business_id=business.id,
                    cycle_id=cycle.id,
                    agent="system",
                    action=activity["action"],
                    detail=activity["detail"],
                )
            )

        total_tasks = len(cycle_result.get("results", []))
        for i, task_result in enumerate(cycle_result.get("results", [])):
            area = task_result.get("area", "general")
            status = task_result.get("status", "completed")
            action_label = self.friendly_action(area) if status != "failed" else f"Failed: {area}"
            session.add(
                Activity(
                    business_id=business.id,
                    cycle_id=cycle.id,
                    agent=area,
                    action=action_label,
                    detail=(task_result.get("result") or "")[:1000],
                )
            )

            task_result["cycle_id"] = cycle.id
            content = self._content_from_result(business, task_result)
            if content:
                session.add(content)

            # Push per-task progress
            progress = ((i + 1) / max(total_tasks, 1)) * 100
            await ws_manager.broadcast_activity(business.id, action_label, area)
            await ws_manager.broadcast_cycle_progress(
                business.id, cycle.id, "running", round(progress, 1),
            )

        await session.flush()

        # --- Post-cycle roadmap operations ---
        await self._post_cycle_roadmap_update(business, cycle, tasks, cycle_result, session)

        kh_artifacts = await self._publish_cycle_results(business, cycle_result)

        failed = cycle_result.get("failed", 0)
        total = cycle_result.get("total", len(tasks))
        cycle.status = "failed" if total > 0 and failed == total else "completed"
        cycle.completed_at = datetime.now(timezone.utc)
        cycle.result = cycle_result
        if kh_artifacts:
            cycle.result["kh_artifacts"] = kh_artifacts

        await self._record_cycle_metrics(business, cycle, cycle_result, session)

        # Fire-and-forget: delegate high-value content to Content Production for ebook expansion
        try:
            await self._maybe_delegate_to_content_production(business, cycle, cycle_result, session)
        except Exception:
            log.debug("Ebook delegation pass failed for %s", business.slug, exc_info=True)

        # Materialize advertising tasks into real campaigns with ad copies
        await self._materialize_advertising_tasks(business, cycle, tasks, cycle_result, session)

        session.add(
            Activity(
                business_id=business.id,
                cycle_id=cycle.id,
                agent="system",
                action="Cycle completed" if cycle.status == "completed" else "Cycle finished with errors",
                detail=f"{total - failed}/{total} tasks succeeded",
            )
        )
        await session.commit()

        duration_s = 0.0
        if cycle.started_at and cycle.completed_at:
            sa = cycle.started_at
            ca = cycle.completed_at
            if sa.tzinfo is None:
                sa = sa.replace(tzinfo=timezone.utc)
            if ca.tzinfo is None:
                ca = ca.replace(tzinfo=timezone.utc)
            duration_s = max(0.0, (ca - sa).total_seconds())

        if cycle.status == "completed":
            pipeline_metrics.record_cycle_complete(
                cycle.trigger, business.plan, duration_s, total,
            )
        else:
            pipeline_metrics.record_cycle_failure(cycle.trigger, business.plan)

        await ws_manager.broadcast_cycle_progress(
            business.id, cycle.id, cycle.status, 100.0,
        )

        # Fire external webhook if registered
        await cycle_webhook_notifier.notify_cycle_complete(
            business.id, cycle.id, cycle.status, cycle.result,
        )

        log.info(
            "Cycle %d complete for %s: %s (%d/%d succeeded)",
            cycle.id, business.slug, cycle.status, total - failed, total,
        )

        # Inject AI-generated content into the deployed template (initial + first few cycles)
        if cycle.trigger in ("initial", "nightly") and cycle.status == "completed":
            try:
                from arclane.provisioning.content_injector import inject_landing_page
                await inject_landing_page(business, session)
            except Exception:
                log.debug("Content injection skipped for %s", business.slug, exc_info=True)

        if cycle.trigger == "on_demand":
            try:
                await send_cycle_complete_email(
                    business.name,
                    business.owner_email,
                    business.slug,
                    tasks_completed=total - failed,
                    tasks_total=total,
                )
            except Exception:
                log.exception("Failed to send cycle complete notification")

            total_working_days = business.working_days_remaining + business.working_days_bonus
            if total_working_days <= 2:
                try:
                    await send_working_days_low_email(
                        business.name,
                        business.owner_email,
                        total_working_days,
                    )
                except Exception:
                    log.exception("Failed to send low working days notification")

        return cycle.result

    async def _post_cycle_roadmap_update(
        self,
        business: Business,
        cycle: Cycle,
        tasks: list[dict],
        cycle_result: dict,
        session: AsyncSession,
    ) -> None:
        """Auto-complete milestones, check graduation, generate advisory notes.

        Note: roadmap_day is advanced daily by the scheduler (_advance_roadmap_days)
        regardless of whether a cycle runs, so it is NOT incremented here.
        """
        if not getattr(business, "current_phase", None) or business.current_phase < 1:
            return

        # Auto-complete milestones based on task results
        from arclane.services.roadmap_service import CORE_TASK_TO_MILESTONE
        for task in tasks:
            milestone_key = task.get("milestone_key")
            # Also check operating_plan core task → milestone mapping
            if not milestone_key:
                task_key = task.get("queue_task_key") or task.get("key", "")
                milestone_key = CORE_TASK_TO_MILESTONE.get(task_key)
            if milestone_key:
                await complete_milestone(
                    business, milestone_key, session,
                    evidence={"cycle_id": cycle.id, "source": "auto"},
                )

        # Record health score snapshot
        try:
            await record_health_snapshot(business, session)
        except Exception:
            log.warning("Health snapshot failed for %s", business.slug, exc_info=True)

        # Feed analytics engine with cycle data
        try:
            from arclane.api.routes.insights import _engine as analytics_engine
            from arclane.analytics.engine import CustomerInsight
            from sqlalchemy import func as sa_func
            from sqlalchemy import select as sa_select
            from arclane.models.tables import Content as ContentModel, Cycle as CycleModel

            cycles_run = (await session.execute(
                sa_select(sa_func.count(CycleModel.id)).where(
                    CycleModel.business_id == business.id,
                    CycleModel.status == "completed",
                )
            )).scalar() or 0
            content_count = (await session.execute(
                sa_select(sa_func.count(ContentModel.id)).where(
                    ContentModel.business_id == business.id,
                )
            )).scalar() or 0

            months_active = max(1, ((business.roadmap_day or 0) + 29) // 30)
            plan_monthly_cents = {
                "preview": 0, "starter": 4900, "pro": 9900,
                "growth": 24900, "scale": 49900,
            }
            ltv_cents = plan_monthly_cents.get(business.plan, 0) * months_active
            engagement = business.health_score or 50.0

            analytics_engine.record_insight(CustomerInsight(
                business_id=business.id,
                plan=business.plan,
                lifetime_value_cents=ltv_cents,
                months_active=months_active,
                total_cycles=cycles_run,
                total_content=content_count,
                features_used=min(10, cycles_run + content_count),
                engagement_score=engagement,
                churn_risk=round(max(0.0, min(1.0, 1.0 - engagement / 100)), 2),
                expansion_potential=round(min(1.0, engagement / 100 * 0.8 + cycles_run * 0.02), 2),
            ))

            # Record journey stage based on phase
            phase_to_stage = {
                1: "onboarding",
                2: "activation",
                3: "engagement",
                4: "conversion",
            }
            stage = phase_to_stage.get(business.current_phase or 0, "awareness")
            analytics_engine.record_journey_event(
                business.id, stage, "cycle_complete",
                metadata={"roadmap_day": business.roadmap_day, "cycle_id": cycle.id},
            )
        except Exception:
            log.debug("Analytics ingestion skipped for %s", business.slug, exc_info=True)

        # Generate advisory notes
        try:
            await generate_advisory_notes(business, session)
        except Exception:
            log.warning("Advisory generation failed for %s", business.slug, exc_info=True)

        # Check phase graduation and auto-advance
        if 1 <= business.current_phase <= 4:
            try:
                check = await check_phase_graduation(business, session)
                if check["ready"]:
                    result = await advance_phase(business, session)
                    if result["advanced"]:
                        session.add(Activity(
                            business_id=business.id,
                            cycle_id=cycle.id,
                            agent="system",
                            action=f"Advanced to Phase {result['to_phase']}" if result["to_phase"] <= 4 else "Program graduated!",
                            detail=f"Phase {result['from_phase']} completed with score {check['score']}%.",
                        ))
                        # Send phase advancement notification
                        try:
                            from arclane.notifications import send_phase_advancement_email
                            await send_phase_advancement_email(
                                business.name, business.owner_email, business.slug,
                                result["from_phase"], result["to_phase"], check["score"],
                            )
                        except Exception:
                            log.warning("Phase advancement email failed for %s", business.slug, exc_info=True)
            except Exception:
                log.warning("Phase graduation check failed for %s", business.slug, exc_info=True)

        await session.flush()

    async def _record_cycle_metrics(
        self,
        business: Business,
        cycle: Cycle,
        cycle_result: dict,
        session: AsyncSession,
    ) -> None:
        """Record simple execution and content metrics after each cycle."""
        total = int(cycle_result.get("total", 0) or 0)
        failed = int(cycle_result.get("failed", 0) or 0)
        succeeded = max(0, total - failed)
        duration_seconds = 0.0
        if cycle.started_at and cycle.completed_at:
            duration_seconds = max(
                0.0,
                (cycle.completed_at - cycle.started_at).total_seconds(),
            )

        total_content = (
            await session.execute(
                select(func.count(Content.id)).where(Content.business_id == business.id)
            )
        ).scalar() or 0
        published_content = (
            await session.execute(
                select(func.count(Content.id))
                .where(Content.business_id == business.id)
                .where(Content.status == "published")
            )
        ).scalar() or 0
        completed_cycles = (
            await session.execute(
                select(func.count(Cycle.id))
                .where(Cycle.business_id == business.id)
                .where(Cycle.status == "completed")
            )
        ).scalar() or 0

        metric_rows = [
            Metric(business_id=business.id, name="tasks_completed", value=float(succeeded)),
            Metric(business_id=business.id, name="tasks_failed", value=float(failed)),
            Metric(business_id=business.id, name="cycle_duration_seconds", value=duration_seconds),
            Metric(business_id=business.id, name="content_total", value=float(total_content)),
            Metric(business_id=business.id, name="content_published", value=float(published_content)),
            Metric(business_id=business.id, name="cycles_completed", value=float(completed_cycles)),
        ]
        for row in metric_rows:
            session.add(row)

    async def _execute_bridge_cycle(self, business: Business, cycle: Cycle, tasks: list[dict]) -> dict:
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                f"{self._csuite_url}/api/v1/arclane/cycle",
                json={
                    "business_id": business.id,
                    "business_slug": business.slug,
                    "business_name": business.name,
                    "business_description": business.description,
                    "cycle_id": cycle.id,
                    "tasks": tasks,
                },
                headers={
                    "Authorization": f"Bearer {self._service_token}",
                    "X-Service-Token": self._service_token,
                    "X-Service-Name": "arclane",
                },
            )
            response.raise_for_status()
            return response.json()

    async def _execute_internal_cycle(
        self,
        business: Business,
        cycle: Cycle,
        tasks: list[dict],
        session: AsyncSession,
    ) -> dict:
        results: list[dict] = []
        failed = 0
        is_initial = cycle.trigger == "initial"
        total_tasks = len(tasks)

        for idx, task in enumerate(tasks, 1):
            try:
                await self._emit_progress_updates(business, cycle, task, session)
                result = await self._execute_internal_task(business, task)
                results.append(result)

                # Send per-task status email during the initial cycle
                if is_initial and result.get("status") == "completed":
                    task_key = task.get("key", "")
                    try:
                        await send_task_complete_email(
                            business_name=business.name,
                            owner_email=business.owner_email,
                            slug=business.slug,
                            task_key=task_key,
                            task_index=idx,
                            task_total=total_tasks,
                            result_snippet=result.get("result"),
                        )
                    except Exception:
                        log.debug("Per-task email failed for %s: %s", business.slug, task_key, exc_info=True)

            except Exception:
                failed += 1
                log.warning("Internal task failed for %s: %s", business.slug, task, exc_info=True)
                results.append(
                    {
                        "area": task.get("area", "general"),
                        "status": "failed",
                        "result": "This task could not be completed automatically.",
                    }
                )

        return {
            "mode": "internal",
            "results": results,
            "failed": failed,
            "total": total_tasks,
        }

    async def _emit_progress_updates(
        self,
        business: Business,
        cycle: Cycle,
        task: dict,
        session: AsyncSession,
    ) -> None:
        for action, detail in self._progress_messages_for_task(task):
            session.add(
                Activity(
                    business_id=business.id,
                    cycle_id=cycle.id,
                    agent="system",
                    action=action,
                    detail=detail,
                )
            )
            await session.commit()

    async def _execute_internal_task(self, business: Business, task: dict) -> dict:
        area = task.get("area", "general")
        prompt_pack = prompt_pack_for_area(area)
        system_prompt = "\n\n".join(
            [ORCHESTRATOR_SYSTEM_PROMPT, prompt_pack["system_prompt"]]
        )
        user_prompt = self._build_user_prompt(business, task, prompt_pack)

        model = self._llm_client.model_for_area(area)
        generated = await self._llm_client.generate(system_prompt, user_prompt, model=model)
        result_text = generated or self._deterministic_output(business, task, prompt_pack)

        result = {
            "area": area,
            "status": "completed",
            "agent": prompt_pack["agent"],
            "result": result_text,
        }

        content_spec = self._infer_content_spec(task)
        if content_spec:
            result["content_type"] = content_spec["content_type"]
            result["content_title"] = content_spec["title"]
            result["content_body"] = generated or self._deterministic_content(business, task, content_spec)

        return result

    async def _publish_cycle_results(self, business: Business, cycle_result: dict) -> list[dict]:
        try:
            kh_refs = await self._kh_publisher.publish_cycle_results(
                business_name=business.name,
                results=cycle_result.get("results", []),
            )
        except Exception:
            log.warning("Knowledge publish failed for %s", business.slug, exc_info=True)
            kh_refs = []

        # Fire-and-forget — never block cycle completion waiting for Nexus
        import asyncio as _asyncio
        _asyncio.create_task(self._nexus_publisher.publish_cycle_insights(
            business_name=business.name,
            business_description=business.description or "",
            cycle_results=cycle_result.get("results", []),
        ))

        return kh_refs

    async def _materialize_advertising_tasks(
        self,
        business: Business,
        cycle: Cycle,
        tasks: list[dict],
        cycle_result: dict,
        session: AsyncSession,
    ) -> None:
        """When a cycle includes advertising tasks, generate real campaigns."""
        ad_tasks = [t for t in tasks if t.get("area") == "advertising"]
        if not ad_tasks:
            return
        try:
            from arclane.services.advertising_service import generate_full_campaign
            for task in ad_tasks:
                platform = task.get("platform", "facebook")
                campaign_type = task.get("campaign_type", "awareness")
                result = await generate_full_campaign(
                    business, session,
                    platform=platform,
                    campaign_type=campaign_type,
                    llm_client=self._llm_client,
                )
                log.info(
                    "Materialized advertising campaign for %s: %s",
                    business.slug, result.get("campaign_name"),
                )
        except Exception:
            log.warning("Advertising materialization failed for %s", business.slug, exc_info=True)

    async def _maybe_delegate_to_content_production(
        self,
        business: Business,
        cycle: Cycle,
        cycle_result: dict,
        session: AsyncSession,
    ) -> None:
        """Delegate high-value cycle content to Content Production for ebook expansion.

        Fires after cycle completion. Scans results for reports or blogs with
        substantial word count, then sends them to Content Production's ebook
        pipeline as fire-and-forget jobs.
        """
        if self._cp_client is None:
            return

        EBOOK_ELIGIBLE_TYPES = {"report", "blog"}
        MIN_WORD_COUNT = 1000

        for task_result in cycle_result.get("results", []):
            if task_result.get("status") != "completed":
                continue

            content_type = task_result.get("content_type", "")
            if content_type not in EBOOK_ELIGIBLE_TYPES:
                continue

            body = task_result.get("content_body") or task_result.get("result") or ""
            word_count = len(body.split())
            if word_count < MIN_WORD_COUNT:
                continue

            topic = task_result.get("content_title") or task_result.get("area", "general")

            # Build marketplace credentials and webhook URL from business config
            agent_config = business.agent_config or {}
            marketplace_credentials = agent_config.get("marketplace_credentials")
            revenue_webhook_url = (
                f"http://localhost:8012/api/businesses/{business.slug}/webhooks/revenue"
            )

            try:
                result = self._cp_client.produce_ebook(
                    topic=topic,
                    category=business.description or "general",
                    audience=business.description or "",
                    priority=0.7,
                    marketplace_credentials=marketplace_credentials if marketplace_credentials else None,
                    revenue_webhook_url=revenue_webhook_url,
                )
                if result:
                    session.add(
                        Activity(
                            business_id=business.id,
                            cycle_id=cycle.id,
                            agent="system",
                            action="Ebook delegated to Content Production",
                            detail=f"Topic: {topic} ({word_count} words)",
                        )
                    )
                    log.info(
                        "Delegated ebook production for %s: %s (%d words)",
                        business.slug, topic, word_count,
                    )
                else:
                    log.debug(
                        "Content Production ebook delegation returned None for %s: %s",
                        business.slug, topic,
                    )
            except Exception:
                log.warning(
                    "Ebook delegation failed for %s: %s",
                    business.slug, topic, exc_info=True,
                )

    async def _mark_cycle_failed(
        self, business: Business, cycle: Cycle, session: AsyncSession
    ) -> dict:
        session.add(
            Activity(
                business_id=business.id,
                cycle_id=cycle.id,
                agent="system",
                action="Cycle failed",
                detail="Processing encountered an issue. Our team has been notified.",
            )
        )
        cycle.status = "failed"
        cycle.completed_at = datetime.now(timezone.utc)
        cycle.result = {"status": "failed"}
        pipeline_metrics.record_cycle_failure(cycle.trigger, business.plan)
        await cycle_webhook_notifier.notify_cycle_complete(
            business.id, cycle.id, "failed", cycle.result,
        )
        await session.commit()
        return cycle.result

    def _build_tasks(self, business: Business, cycle_trigger: str | None = None) -> list[dict]:
        """Build task list, preferring AIL workflow when available."""
        operating_plan = (business.agent_config or {}).get("operating_plan") or {}
        planned_tasks = operating_plan.get("agent_tasks") or []
        if planned_tasks:
            if not any("queue_status" in item for item in planned_tasks):
                return [
                    {
                        "area": item.get("area", "general"),
                        "action": item.get("action", "execute_task"),
                        "description": item.get("description", ""),
                        "brief": item.get("brief", "Advance the business"),
                        "intake_brief": operating_plan.get("intake_brief") or {},
                    }
                    for item in planned_tasks
                ]
            # Initial cycle: run ALL pending tasks sequentially for instant Day 1 value
            if cycle_trigger == "initial":
                all_tasks = self._select_all_operating_plan_tasks(operating_plan)
                if all_tasks:
                    return all_tasks
            selected = self._select_operating_plan_task(operating_plan)
            if selected:
                return [selected]
            # All operating plan tasks done — fall through to roadmap phase tasks
        # Check for roadmap phase tasks (Day 8+ when core program is done)
        current_phase = getattr(business, "current_phase", None) or 0
        if current_phase >= 1:
            if current_phase >= 5:
                # Post-graduation: use adaptive optimizer
                from arclane.services.ongoing_optimizer import select_adaptive_task
                # Can't await in sync method — return marker for async handling
                return [{"_roadmap_async": True, "_phase": 5}]
            return [{"_roadmap_async": True, "_phase": current_phase}]
        if self._workflow_service.optimizer_available:
            workflow_name = self._workflow_service.workflow_for_template(business.template)
            if workflow_name:
                try:
                    tasks = self._workflow_service.workflow_to_tasks(
                        workflow_name, business.description
                    )
                    if tasks:
                        log.info(
                            "Using workflow '%s' -> %d tasks for %s",
                            workflow_name, len(tasks), business.slug,
                        )
                        return tasks
                except Exception:
                    log.warning(
                        "Workflow '%s' failed, falling back to static plan",
                        workflow_name, exc_info=True,
                    )

        intake_brief = build_intake_brief(
            business.description,
            website_summary=getattr(business, "website_summary", None),
            website_url=getattr(business, "website_url", None),
        )
        full_plan = build_task_plan(
            business.description,
            getattr(business, "template", None),
            website_summary=getattr(business, "website_summary", None),
            website_url=getattr(business, "website_url", None),
        )
        for task in full_plan["tasks"]:
            task.setdefault("intake_brief", intake_brief)
        return full_plan["tasks"]

    def next_queue_task(self, business: Business) -> dict | None:
        """Return the next eligible queued task from the persisted operating plan."""
        operating_plan = (business.agent_config or {}).get("operating_plan") or {}
        planned_tasks = operating_plan.get("agent_tasks") or []
        if not planned_tasks or not any("queue_status" in item for item in planned_tasks):
            return None
        return self._select_operating_plan_task(operating_plan)

    def _build_user_prompt(self, business: Business, task: dict, prompt_pack: dict) -> str:
        website_summary = business.website_summary or "No website baseline provided."
        website_url = business.website_url or "Not provided"
        intake_brief = task.get("intake_brief") or build_intake_brief(
            business.description,
            website_summary=business.website_summary,
            website_url=business.website_url,
        )
        research_steps = "; ".join(intake_brief.get("instructions", []))
        proof_targets = "; ".join(intake_brief.get("visible_proof_targets", []))
        provisioning_targets = "; ".join(intake_brief.get("provisioning_requirements", []))

        # Phase-aware context
        phase = task.get("phase") or getattr(business, "current_phase", 0) or 0
        day = getattr(business, "roadmap_day", 0) or 0
        health = getattr(business, "health_score", None)
        phase_block = phase_context_block(phase, day, health) if phase >= 1 else ""

        # Milestone context — what this task delivers
        milestone_key = task.get("milestone_key", "")
        milestone_line = f"Milestone: {milestone_key}" if milestone_key else ""

        queue_guidance = ""
        if task.get("duration_days", 1) > 1:
            queue_guidance = (
                f"\nQueue execution: night {task.get('night_index', 1)} of {task.get('duration_days', 1)}."
            )
            if task.get("is_final_pass"):
                queue_guidance += (
                    " This is the final pass for this output. Deliver the finished artifact in polished, "
                    "plain language."
                )
            else:
                queue_guidance += (
                    " This output is intentionally stretched across multiple nights. Do not deliver the final "
                    "artifact yet. Return a concise operator update covering progress made tonight, what remains, "
                    "and any notable findings."
                )
        return (
            f"Business name: {business.name}\n"
            f"Task area: {task.get('area', 'general')}\n"
            f"Executive lens: {prompt_pack['executive']}\n"
            f"Program type: {task.get('program_type', 'general')}\n"
            f"{phase_block}\n"
            f"{milestone_line}\n"
            f"Business brief: {business.description}\n"
            f"Website URL: {website_url}\n"
            f"Website summary: {website_summary}\n"
            f"Intake research checklist: {research_steps}\n"
            f"Visible proof targets: {proof_targets}\n"
            f"Provisioning requirements: {provisioning_targets}\n"
            f"Specialist brief: {task.get('brief', 'Advance the business')}\n"
            f"Requested task: {task.get('description', '')}\n"
            f"{queue_guidance}\n\n"
            "Return practical output in plain language. If this is content work, provide publishable copy "
            "the founder can use today — no placeholders. If this is analysis, provide specific recommendations "
            "with next actions. If this is a system or playbook, make it complete enough that someone could "
            "follow it without asking questions. Make sure your output supports visible proof of work and "
            "does not ignore provisioning dependencies."
        )

    def _deterministic_output(self, business: Business, task: dict, prompt_pack: dict) -> str:
        summary = business.website_summary or "No existing website was analyzed."
        task_line = task.get("description", "Advance the business.")
        area = task.get("area", "general")
        if task.get("duration_days", 1) > 1 and not task.get("is_final_pass"):
            return (
                f"Night {task.get('night_index', 1)} of {task.get('duration_days', 1)}: "
                f"advanced {task.get('title', 'the current output')}.\n"
                f"Completed tonight: {task.get('brief', 'Moved the queued work forward.')}\n"
                "What remains: finish the remaining sections, tighten the business case, and prepare the final "
                "artifact for the next queued pass.\n"
                f"Context carried forward: {summary}"
            )

        if area == "strategy":
            return (
                f"Offer: {business.name} should lead with a simple, outcome-focused offer.\n"
                f"Wedge: Start where the customer pain is urgent and easy to explain.\n"
                f"Priority: {task_line}\n"
                f"Next actions: tighten the promise, define one acquisition channel, and ship one fast test.\n"
                f"Baseline: {summary}"
            )
        if area == "market_research":
            return (
                f"Market read: identify who already serves this buyer, where their messaging is weak, and where speed wins.\n"
                f"Immediate gap to exploit: clearer positioning and faster proof of value.\n"
                f"Research target: {task_line}\n"
                f"Current website baseline: {summary}"
            )
        if area == "operations":
            return (
                f"Operational focus: remove the slowest handoff between traffic, lead capture, and follow-up.\n"
                f"Recommended workflow: collect intent, tag source, trigger follow-up, and review weekly.\n"
                f"Optimization brief: {task_line}\n"
                f"Current baseline: {summary}"
            )
        if area == "engineering":
            return (
                f"Build scope: keep the first release narrow, measurable, and shippable.\n"
                f"Primary task: {task_line}\n"
                f"Technical direction: solve one user problem end-to-end before expanding surface area."
            )
        if area == "finance":
            return (
                f"Financial read: price for margin, cap avoidable spend, and track payback tightly.\n"
                f"Task focus: {task_line}\n"
                f"Immediate control point: keep CAC and execution cost below the first month gross profit."
            )

        # Phase 2-4 action-specific fallbacks
        action = task.get("action", "")

        if action == "create_validation_plan":
            return (
                f"# Validation plan for {business.name}\n\n"
                "## Hypothesis 1: Target customer\n"
                "- Test: Run 5 customer discovery interviews in the next 7 days\n"
                "- Metric: 3/5 interviewees describe the problem unprompted\n"
                "- Decision: If yes, double down on this segment. If no, expand the target.\n\n"
                "## Hypothesis 2: Channel\n"
                "- Test: Publish 5 pieces of content on the primary channel\n"
                "- Metric: 2%+ engagement rate\n"
                "- Decision: If yes, scale content frequency. If no, test a different channel.\n\n"
                "## Hypothesis 3: Pricing\n"
                f"- Test: Present the pricing page to 10 prospects\n"
                "- Metric: 3/10 say 'fair' or 'good value'\n"
                "- Decision: If yes, lock pricing. If no, adjust positioning or add a lower tier.\n"
            )

        if action in ("create_pitch_deck", "create_full_deck"):
            return (
                f"# Pitch deck outline for {business.name}\n\n"
                f"**Slide 1 — Title:** {business.name}\n"
                f"**Slide 2 — Problem:** {business.description[:200]}\n"
                "**Slide 3 — Solution:** How we solve it better than alternatives\n"
                "**Slide 4 — Market:** Target market size and growth\n"
                "**Slide 5 — Business model:** Revenue model and pricing\n"
                "**Slide 6 — Traction:** Key metrics and milestones achieved\n"
                "**Slide 7 — Competition:** Positioning vs. alternatives\n"
                "**Slide 8 — Go-to-market:** Primary acquisition channel and strategy\n"
                "**Slide 9 — Team:** Founder background and key hires planned\n"
                "**Slide 10 — Ask:** Funding amount and use of funds\n"
            )

        if action == "create_email_sequence":
            return (
                f"# 5-email nurture sequence for {business.name}\n\n"
                "## Email 1 (Day 0) — Welcome\n"
                f"Subject: Welcome to {business.name}\n"
                f"Body: Thank you for signing up. Here's what {business.name} does and what to expect.\n\n"
                "## Email 2 (Day 2) — Value\n"
                "Subject: The one thing most people get wrong about [topic]\n"
                "Body: Share a useful insight that demonstrates expertise.\n\n"
                "## Email 3 (Day 5) — Social proof\n"
                "Subject: How [customer type] solved [problem]\n"
                "Body: Case study or example of the solution working.\n\n"
                "## Email 4 (Day 8) — Objection handling\n"
                "Subject: Is [common objection] holding you back?\n"
                "Body: Address the top reason people don't buy.\n\n"
                "## Email 5 (Day 12) — Soft CTA\n"
                f"Subject: Ready to try {business.name}?\n"
                "Body: Make the offer with a clear next step.\n"
            )

        if action == "create_brand_guide":
            return (
                f"# Brand voice guide for {business.name}\n\n"
                "## Voice attributes\n"
                "- **Direct:** Say what you mean in the fewest words. No filler.\n"
                "- **Warm:** Speak like a trusted advisor, not a corporation.\n"
                "- **Expert:** Show knowledge through specifics, not jargon.\n\n"
                "## Messaging pillars\n"
                f"1. {business.name} makes [outcome] simple.\n"
                "2. Results speak louder than promises.\n"
                "3. Built for [target customer], not everyone.\n\n"
                "## Vocabulary\n"
                "- USE: simple, fast, clear, proven, specific\n"
                "- AVOID: synergy, leverage, disrupt, innovative, revolutionary\n"
            )

        if action == "create_hiring_plan":
            return (
                f"# First hire plan for {business.name}\n\n"
                "## Role: [Title based on bottleneck]\n"
                "- Type: Part-time contractor → full-time if it works\n"
                "- Must-have skills: [3 specific skills]\n"
                "- Nice-to-have: [2 bonus skills]\n\n"
                "## Compensation\n"
                "- Range: Based on market rate for this role\n"
                "- Structure: Fixed monthly + performance bonus tied to one metric\n\n"
                "## Sourcing\n"
                "- Post on: relevant job boards and communities\n"
                "- Reach out to: 5 people in your network who might know candidates\n"
                "- Timeline: Source this week, interview next week, start within 30 days\n"
            )

        if action == "generate_90day_report":
            return (
                f"# 90-day progress report for {business.name}\n\n"
                "## Executive summary\n"
                f"{business.name} completed the 90-day incubator program.\n\n"
                "## Key metrics\n"
                "- Content produced: [count]\n"
                "- Cycles completed: [count]\n"
                "- Revenue: [amount]\n\n"
                "## What worked\n"
                "- [Top performing channel or strategy]\n\n"
                "## What to improve\n"
                "- [Biggest gap or underperformance]\n\n"
                "## Recommendation\n"
                "- Focus Q2 on the highest-ROI activities identified during the program.\n"
            )

        if action == "create_retention_playbook":
            return (
                f"# Customer retention playbook for {business.name}\n\n"
                "## Onboarding (Days 1-7)\n"
                "- Day 1: Welcome email with quick-start guide\n"
                "- Day 3: Check-in email asking about first experience\n"
                "- Day 7: Value reminder with a specific use case\n\n"
                "## Engagement signals\n"
                "- Healthy: logs in weekly, uses core feature, opens emails\n"
                "- At risk: no login in 14 days, ignores emails, support ticket without resolution\n\n"
                "## Churn prevention\n"
                "- At-risk trigger: Send personal email from founder\n"
                "- Pre-churn trigger: Offer discount or call\n"
                "- Post-churn: Win-back sequence (3 emails over 30 days)\n"
            )

        return (
            f"{prompt_pack['executive']} summary:\n"
            f"Task: {task_line}\n"
            f"Business: {business.description}\n"
            "Recommended next move: complete the smallest high-leverage action, then review feedback and iterate."
        )

    def _progress_messages_for_task(self, task: dict) -> list[tuple[str, str]]:
        area = task.get("area", "general")
        brief = task.get("brief", "Advancing the business.")
        progress_suffix = self._task_progress_suffix(task)
        if area == "strategy":
            return [
                ("Updating task list...", "Re-prioritizing the current work queue."),
                ("Reviewing documents...", "Reading the business brief and existing context."),
                ("Structuring strategy brief...", f"{brief}{progress_suffix}"),
            ]
        if area == "market_research":
            return [
                ("Updating task list...", "Refreshing research objectives."),
                ("Searching market...", "Collecting competitor, customer, and positioning signals."),
                ("Reviewing documents...", f"{brief}{progress_suffix}"),
            ]
        if area == "content":
            return [
                ("Updating task list...", "Selecting the highest-visibility asset to create next."),
                ("Drafting deliverable...", "Writing a user-facing asset that proves momentum quickly."),
                ("Saving report...", f"{brief}{progress_suffix}"),
            ]
        if area == "operations":
            return [
                ("Updating task list...", "Syncing operations and launch dependencies."),
                ("Managing infrastructure...", "Checking provisioning, channel readiness, and follow-up flow."),
                ("Coordinating launch workflow...", f"{brief}{progress_suffix}"),
            ]
        return [
            ("Updating task list...", "Refreshing the work queue."),
            ("Reviewing documents...", f"{brief}{progress_suffix}"),
        ]

    def _infer_content_spec(self, task: dict) -> dict | None:
        if not task.get("is_final_pass", True):
            return None

        area = task.get("area", "general")
        action = task.get("action", "")
        description = f"{action} {task.get('description', '')}".lower()
        title = task.get("title") or ""

        # --- Explicit action-based matching (Phase 2-4 tasks) ---

        # Pitch decks and investor materials
        if action in ("create_pitch_deck", "create_full_deck"):
            return {"content_type": "report", "title": title or "Pitch deck"}

        # Email sequences and outreach
        if action in ("create_email_sequence", "create_outreach_templates"):
            return {"content_type": "newsletter", "title": title or "Email sequence"}

        # Brand and style guides
        if action == "create_brand_guide":
            return {"content_type": "report", "title": title or "Brand voice guide"}

        # Content calendars
        if action == "create_content_calendar":
            return {"content_type": "report", "title": title or "Content calendar"}

        # Content batches (social + email)
        if action == "create_content_batch":
            return {"content_type": "social", "title": title or "Content batch"}

        # Validation and strategy plans
        if action in ("create_validation_plan", "define_kpis", "refine_positioning",
                       "design_growth_experiment", "assess_scalability", "create_q2_plan"):
            return {"content_type": "report", "title": title or "Strategy report"}

        # Financial tasks
        if action in ("build_financial_model", "validate_pricing", "setup_revenue_tracking",
                       "create_investor_brief", "analyze_revenue"):
            return {"content_type": "report", "title": title or "Financial report"}

        # Operations tasks
        if action in ("design_lead_capture", "create_ad_brief", "setup_distribution",
                       "analyze_funnel", "create_acquisition_playbook", "optimize_conversion",
                       "recommend_automation", "create_hiring_plan", "create_retention_playbook"):
            return {"content_type": "report", "title": title or "Operations brief"}

        # Market research tasks
        if action in ("competitor_profiling", "seo_baseline", "create_interview_guide",
                       "identify_partners", "setup_competitor_monitor", "competitive_analysis"):
            return {"content_type": "report", "title": title or "Market research report"}

        # Landing page tasks
        if action in ("landing_page_v2", "refresh_brand_content"):
            return {"content_type": "blog", "title": title or "Landing page draft"}

        # 90-day report and quarterly plans
        if action in ("generate_90day_report", "create_quarterly_plan"):
            return {"content_type": "report", "title": title or "Progress report"}

        # Ongoing optimizer tasks
        if action in ("optimize_distribution", "review_retention"):
            return {"content_type": "report", "title": title or "Operations review"}

        # --- Original area-based fallback matching ---

        if area == "market_research":
            return {"content_type": "report", "title": title or "Market research report"}
        if area == "strategy":
            return {"content_type": "report", "title": title or "Mission and positioning brief"}
        if area == "operations" and any(
            keyword in description for keyword in ["ad", "campaign", "funnel", "conversion", "workflow"]
        ):
            return {"content_type": "report", "title": title or "Ad launch brief"}
        if "newsletter" in description:
            content_type = "newsletter"
        elif any(keyword in description for keyword in ["social", "linkedin", "twitter", "x post"]):
            content_type = "social"
        elif any(keyword in description for keyword in ["homepage", "landing page", "website", "page copy"]):
            content_type = "blog"
        elif area == "content":
            content_type = "blog"
        elif any(keyword in description for keyword in ["blog", "article", "homepage", "landing page", "copy"]):
            content_type = "blog"
        elif area in ("operations", "engineering", "finance"):
            return {"content_type": "report", "title": title or f"{area.replace('_', ' ').title()} brief"}
        else:
            return None

        title_seed = (title or task.get("description", "Growth asset")).strip().split(".")[0][:80]
        return {
            "content_type": content_type,
            "title": title_seed or "Growth asset draft",
        }

    def _deterministic_content(self, business: Business, task: dict, content_spec: dict) -> str:
        if content_spec["content_type"] == "report":
            title = content_spec["title"]
            task_text = task.get("description", "")
            if title == "Mission and positioning brief":
                return (
                    f"# Mission and positioning brief for {business.name}\n\n"
                    "## Mission statement\n"
                    f"{business.name} exists to help customers achieve a clear business outcome faster and with less friction.\n\n"
                    "## Positioning\n"
                    f"- Core offer: {business.description}\n"
                    "- Wedge: lead with the most urgent problem visible on the website\n"
                    "- Proof: show faster results, simpler execution, and a clearer call to action\n\n"
                    "## Immediate fixes\n"
                    f"- {task_text}\n"
                    "- Tighten the homepage promise\n"
                    "- Put one offer and one CTA above the fold\n"
                )
            if title == "Market research report":
                return (
                    f"# Market research report for {business.name}\n\n"
                    "## Market read\n"
                    "- Identify direct competitors already speaking to the same buyer\n"
                    "- Look for weak messaging, vague promises, and slow follow-up paths\n"
                    "- Win by being clearer, faster, and easier to trust\n\n"
                    "## Likely opportunities\n"
                    "- Sharper positioning around one buyer segment\n"
                    "- Better conversion copy tied to an immediate pain point\n"
                    "- Faster content and outreach loops built from the site\n\n"
                    f"## Task context\n{task_text}\n"
                )
            return (
                f"# Ad launch brief for {business.name}\n\n"
                "## Recommended campaign\n"
                "- Objective: drive qualified traffic to the clearest offer page\n"
                "- Audience: buyers already experiencing the problem solved by the core offer\n"
                "- CTA: start with one direct conversion action\n\n"
                "## Ad angles\n"
                "- Pain: name the expensive bottleneck\n"
                "- Speed: show how quickly the customer gets value\n"
                "- Clarity: explain the offer in one sentence\n\n"
                "## Next step\n"
                "Use the Run Ads task to generate headlines, body copy, and a starter budget."
            )
        if content_spec["content_type"] == "newsletter":
            return (
                f"Subject: What {business.name} is fixing right now\n\n"
                f"We are focused on one clear outcome: {business.description}\n\n"
                "This week:\n"
                "- Clarified the core offer\n"
                "- Tightened the conversion path\n"
                "- Prepared the next growth test\n\n"
                "Reply with the biggest blocker in your current workflow."
            )

        if content_spec["content_type"] == "social":
            return (
                f"{business.name} exists to make one thing easier: {business.description}\n\n"
                "Most companies lose momentum because the offer is unclear and the next step is buried.\n"
                "We are tightening the message, simplifying the path to action, and shipping faster from the current site.\n\n"
                "If that sounds familiar, start with the bottleneck customers feel first."
            )

        return (
            f"# {business.name}: a clearer path to results\n\n"
            f"{business.description}\n\n"
            "## Why this matters\n"
            "Most businesses lose momentum because the value proposition is too broad and the first action is unclear.\n\n"
            "## What to do next\n"
            f"- Tighten the promise around this task: {task.get('description', '')}\n"
            "- Put one concrete CTA above the fold\n"
            "- Follow up every lead with a short, direct next step\n"
        )

    def _content_from_result(self, business: Business, task_result: dict) -> Content | None:
        content_type = task_result.get("content_type")
        content_body = task_result.get("content_body")
        if not content_type or not content_body:
            return None

        # Tag with roadmap context for attribution
        phase = getattr(business, "current_phase", None) or 0
        milestone_key = task_result.get("milestone_key") or task_result.get("queue_task_key")
        metadata = {
            "phase": phase,
            "roadmap_day": getattr(business, "roadmap_day", None) or 0,
        }
        cycle_id = task_result.get("cycle_id")
        if cycle_id:
            metadata["cycle_id"] = cycle_id

        return Content(
            business_id=business.id,
            content_type=content_type,
            title=task_result.get("content_title"),
            body=content_body,
            status="draft",
            milestone_key=milestone_key,
            metadata_json=metadata,
        )

    def _cycle_queue_label(self, tasks: list[dict]) -> str:
        if not tasks:
            return "No queued work was eligible for this cycle."
        task = tasks[0]
        if task.get("queue_task_key"):
            return (
                f"Advancing {task.get('title', 'queued work')} "
                f"(night {task.get('night_index', 1)}/{task.get('duration_days', 1)})"
            )
        return f"{len(tasks)} tasks queued"

    def _select_operating_plan_task(self, operating_plan: dict) -> dict | None:
        agent_tasks = operating_plan.get("agent_tasks") or []
        completed = {
            task.get("key")
            for task in agent_tasks
            if task.get("queue_status") == "completed"
        }
        for item in agent_tasks:
            if item.get("queue_status") not in {"pending", "queued", "active"}:
                continue
            if any(dep not in completed for dep in item.get("depends_on", [])):
                continue
            selected = deepcopy(item)
            selected["queue_task_key"] = item.get("key")
            selected["intake_brief"] = operating_plan.get("intake_brief") or {}
            selected["program_type"] = operating_plan.get("program_type", "general")
            selected["night_index"] = max(
                1,
                int(selected.get("duration_days", 1)) - int(selected.get("days_remaining", 1)) + 1,
            )
            selected["is_final_pass"] = int(selected.get("days_remaining", 1)) <= 1
            return selected
        return None

    def _select_all_operating_plan_tasks(self, operating_plan: dict) -> list[dict]:
        """Select all pending operating plan tasks for execution in one cycle.

        Used during the initial signup cycle to deliver strategy brief,
        market research, landing page, and launch tweet sequentially.
        """
        agent_tasks = operating_plan.get("agent_tasks") or []
        selected = []
        for item in agent_tasks:
            if item.get("queue_status") not in {"pending", "queued"}:
                continue
            task = deepcopy(item)
            task["queue_task_key"] = item.get("key")
            task["intake_brief"] = operating_plan.get("intake_brief") or {}
            task["program_type"] = operating_plan.get("program_type", "general")
            task["night_index"] = 1
            task["is_final_pass"] = True
            selected.append(task)
        return selected

    def _sync_operating_plan_after_cycle(
        self,
        business: Business,
        tasks: list[dict],
        cycle_result: dict,
    ) -> list[dict[str, str]]:
        operating_plan = deepcopy((business.agent_config or {}).get("operating_plan") or {})
        if not operating_plan or not tasks:
            return []

        agent_tasks = operating_plan.get("agent_tasks") or []
        add_on_offers = operating_plan.get("add_on_offers") or []
        activities: list[dict[str, str]] = []
        results = cycle_result.get("results", [])

        for task, result in zip(tasks, results):
            plan_task_key = task.get("queue_task_key")
            if not plan_task_key:
                continue

            plan_task = next(
                (item for item in agent_tasks if item.get("key") == plan_task_key),
                None,
            )
            if not plan_task:
                continue

            if result.get("status") == "failed":
                plan_task["queue_status"] = "active" if plan_task.get("queue_status") == "active" else "pending"
                continue

            remaining = max(int(plan_task.get("days_remaining", 1)) - 1, 0)
            plan_task["days_remaining"] = remaining
            result["queue_task_key"] = plan_task_key
            result["night_index"] = task.get("night_index", 1)
            result["duration_days"] = task.get("duration_days", 1)

            matching_offer = next(
                (offer for offer in add_on_offers if offer.get("key") == plan_task.get("output_key")),
                None,
            )

            if remaining > 0:
                plan_task["queue_status"] = "active"
                result["status"] = "in_progress"
                if matching_offer and matching_offer.get("status") == "purchased":
                    matching_offer["status"] = "in_progress"
                continue

            plan_task["queue_status"] = "completed"
            if matching_offer and matching_offer.get("status") in {"purchased", "in_progress"}:
                matching_offer["status"] = "completed"

            for offer in add_on_offers:
                if (
                    offer.get("trigger_output_key") == plan_task.get("output_key")
                    and offer.get("status") == "locked"
                ):
                    offer["status"] = "available"
                    activities.append(
                        {
                            "action": "Add-on available",
                            "detail": (
                                f"{offer.get('title', 'Next package')} is now available and can cut ahead "
                                "of the normal queue."
                            ),
                        }
                    )

        updated_agent_config = deepcopy(business.agent_config or {})
        updated_agent_config["operating_plan"] = operating_plan
        business.agent_config = updated_agent_config
        return activities

    def _task_progress_suffix(self, task: dict) -> str:
        if task.get("duration_days", 1) <= 1:
            return ""
        return f" (night {task.get('night_index', 1)} of {task.get('duration_days', 1)})"

    def friendly_action(self, agent_or_area: str) -> str:
        """Convert internal agent/area name to user-friendly action label."""
        key = agent_or_area.lower()
        if key in AGENT_ACTION_MAP:
            return AGENT_ACTION_MAP[key]
        area_map = {
            "strategy": "Analyzing strategy",
            "market_research": "Researching market",
            "content": "Creating content",
            "operations": "Setting up operations",
            "engineering": "Building features",
            "security": "Reviewing security",
            "finance": "Analyzing finances",
            "general": "Working on your business",
        }
        return area_map.get(key, "Working on your business")


orchestrator = ArclaneOrchestrator()
