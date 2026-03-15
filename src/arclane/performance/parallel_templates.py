"""Parallel template instantiation.

Item 150: Instantiates multiple template files concurrently when setting up
a business workspace, reducing total provisioning time.
"""

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arclane.core.logging import get_logger

log = get_logger("performance.parallel_templates")


@dataclass
class InstantiationResult:
    """Result of instantiating a single template file."""
    source: str
    destination: str
    elapsed_s: float = 0.0
    success: bool = True
    error: str | None = None
    size_bytes: int = 0


@dataclass
class ParallelInstantiationResult:
    """Aggregate result of parallel template instantiation."""
    total_files: int = 0
    succeeded: int = 0
    failed: int = 0
    total_elapsed_s: float = 0.0
    max_parallel: int = 0
    results: list[InstantiationResult] = field(default_factory=list)


class ParallelTemplateInstantiator:
    """Instantiates template files in parallel using asyncio.

    Instead of copying files sequentially, groups them by directory
    and copies within each directory concurrently.
    """

    def __init__(self, max_concurrency: int = 8):
        self._max_concurrency = max_concurrency
        self._instantiation_count = 0

    @property
    def instantiation_count(self) -> int:
        return self._instantiation_count

    def reset_stats(self) -> None:
        self._instantiation_count = 0

    async def _copy_file(
        self,
        source: Path,
        destination: Path,
        semaphore: asyncio.Semaphore,
        variables: dict[str, str] | None = None,
    ) -> InstantiationResult:
        """Copy a single file, optionally substituting variables."""
        start = time.monotonic()
        result = InstantiationResult(
            source=str(source),
            destination=str(destination),
        )

        async with semaphore:
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)

                if variables and source.suffix in (".html", ".js", ".css", ".json", ".yml", ".yaml", ".env", ".md"):
                    # Template substitution for text files
                    content = await asyncio.to_thread(source.read_text, "utf-8")
                    for key, value in variables.items():
                        content = content.replace(f"{{{{ {key} }}}}", value)
                        content = content.replace(f"{{{{{key}}}}}", value)
                    await asyncio.to_thread(destination.write_text, content, "utf-8")
                else:
                    # Binary copy
                    await asyncio.to_thread(shutil.copy2, source, destination)

                result.size_bytes = destination.stat().st_size if destination.exists() else 0
                result.success = True
            except Exception as exc:
                result.success = False
                result.error = str(exc)
                log.warning("Failed to copy %s: %s", source, exc)

        result.elapsed_s = time.monotonic() - start
        return result

    async def instantiate(
        self,
        template_dir: Path,
        workspace_dir: Path,
        variables: dict[str, str] | None = None,
    ) -> ParallelInstantiationResult:
        """Instantiate all files from a template directory in parallel.

        Args:
            template_dir: Source template directory.
            workspace_dir: Destination workspace directory.
            variables: Template variables to substitute (key -> value).

        Returns:
            ParallelInstantiationResult with per-file results.
        """
        overall_start = time.monotonic()
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Collect all files to copy
        files: list[tuple[Path, Path]] = []
        for source_file in template_dir.rglob("*"):
            if source_file.is_file():
                relative = source_file.relative_to(template_dir)
                dest_file = workspace_dir / relative
                files.append((source_file, dest_file))

        if not files:
            return ParallelInstantiationResult()

        semaphore = asyncio.Semaphore(self._max_concurrency)
        tasks = [
            self._copy_file(src, dst, semaphore, variables)
            for src, dst in files
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        aggregate = ParallelInstantiationResult(
            total_files=len(files),
            max_parallel=self._max_concurrency,
        )

        for r in results:
            if isinstance(r, Exception):
                aggregate.failed += 1
                aggregate.results.append(InstantiationResult(
                    source="unknown", destination="unknown",
                    success=False, error=str(r),
                ))
            else:
                if r.success:
                    aggregate.succeeded += 1
                else:
                    aggregate.failed += 1
                aggregate.results.append(r)

        aggregate.total_elapsed_s = time.monotonic() - overall_start
        self._instantiation_count += 1

        log.info(
            "Parallel instantiation: %d/%d files in %.3fs (max_parallel=%d)",
            aggregate.succeeded, aggregate.total_files,
            aggregate.total_elapsed_s, self._max_concurrency,
        )

        return aggregate


# Singleton
parallel_instantiator = ParallelTemplateInstantiator()
