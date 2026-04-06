"""
V18 Strategy Evolution Engine — Genetic crossover of top Strategy DNA.

Treats strategy parameters as genomes. Selects top performers, crosses them,
applies mutation, and produces offspring strategies for shadow-testing.
Inspired by genetic programming (Koza, 1992) applied to quantitative finance.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("algochains.strategy_evolution")


@dataclass
class StrategyGenome:
    """A strategy represented as a set of evolvable parameters (genes)."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = ""
    parent_ids: list[str] = field(default_factory=list)
    generation: int = 0
    genes: dict[str, float] = field(default_factory=dict)
    fitness: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    evaluated: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "parent_ids": self.parent_ids,
            "generation": self.generation,
            "genes": self.genes,
            "fitness": round(self.fitness, 4),
            "sharpe": round(self.sharpe, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "win_rate": round(self.win_rate, 4),
            "trade_count": self.trade_count,
            "evaluated": self.evaluated,
        }


# ── Gene definitions with valid ranges ────────────────────────

GENE_RANGES: dict[str, tuple[float, float]] = {
    "rsi_period": (5, 30),
    "rsi_overbought": (65, 85),
    "rsi_oversold": (15, 35),
    "ema_fast": (5, 20),
    "ema_slow": (20, 100),
    "bb_period": (10, 30),
    "bb_std": (1.5, 3.0),
    "atr_period": (7, 21),
    "atr_multiplier": (1.0, 4.0),
    "stop_loss_pct": (0.5, 5.0),
    "take_profit_pct": (1.0, 10.0),
    "position_size_pct": (1.0, 10.0),
    "volume_threshold": (0.5, 3.0),
    "momentum_weight": (0.1, 0.9),
    "mean_reversion_weight": (0.1, 0.9),
    "trend_strength_min": (0.1, 0.8),
    "max_holding_periods": (1, 50),
    "confidence_threshold": (0.3, 0.9),
}

STRATEGY_TEMPLATES: dict[str, list[str]] = {
    "momentum": [
        "ema_fast", "ema_slow", "momentum_weight", "volume_threshold",
        "stop_loss_pct", "take_profit_pct", "position_size_pct", "trend_strength_min",
    ],
    "mean_reversion": [
        "rsi_period", "rsi_overbought", "rsi_oversold", "bb_period", "bb_std",
        "mean_reversion_weight", "stop_loss_pct", "take_profit_pct", "position_size_pct",
    ],
    "breakout": [
        "bb_period", "bb_std", "atr_period", "atr_multiplier", "volume_threshold",
        "stop_loss_pct", "take_profit_pct", "position_size_pct", "confidence_threshold",
    ],
    "scalper": [
        "ema_fast", "rsi_period", "atr_period", "atr_multiplier",
        "stop_loss_pct", "take_profit_pct", "position_size_pct", "max_holding_periods",
    ],
}


class StrategyEvolutionEngine:
    """Evolve trading strategies using genetic algorithms.

    Population management:
    - Tournament selection for parent choice
    - Uniform crossover for gene mixing
    - Gaussian mutation for exploration
    - Elitism to preserve top performers
    """

    def __init__(
        self,
        population_size: int = 50,
        mutation_rate: float = 0.15,
        crossover_rate: float = 0.7,
        elite_pct: float = 0.1,
    ):
        self.pop_size = population_size
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.elite_count = max(1, int(population_size * elite_pct))
        self._population: list[StrategyGenome] = []
        self._generation = 0
        self._history: list[dict] = []

    async def initialize_population(
        self,
        strategy_type: str = "momentum",
        seeds: Optional[list[dict]] = None,
    ) -> dict:
        """Create initial population from template or seed strategies."""
        gene_names = STRATEGY_TEMPLATES.get(strategy_type, STRATEGY_TEMPLATES["momentum"])

        self._population = []
        self._generation = 0

        # Seed from provided strategies
        if seeds:
            for i, seed in enumerate(seeds[:self.pop_size // 2]):
                genome = StrategyGenome(
                    name=f"seed_{i}",
                    genes={g: seed.get(g, self._random_gene(g)) for g in gene_names},
                    generation=0,
                )
                self._population.append(genome)

        # Fill remaining with random genomes
        while len(self._population) < self.pop_size:
            genome = StrategyGenome(
                name=f"gen0_{len(self._population)}",
                genes={g: self._random_gene(g) for g in gene_names},
                generation=0,
            )
            self._population.append(genome)

        logger.info(
            "Initialized population: %d genomes, %d genes, type=%s",
            len(self._population), len(gene_names), strategy_type,
        )
        return {
            "population_size": len(self._population),
            "gene_count": len(gene_names),
            "genes": gene_names,
            "strategy_type": strategy_type,
            "seeded": len(seeds) if seeds else 0,
        }

    async def evaluate(self, genome_id: str, metrics: dict) -> dict:
        """Record evaluation metrics for a genome after backtesting."""
        genome = self._find_genome(genome_id)
        if not genome:
            return {"error": f"Genome '{genome_id}' not found"}

        genome.sharpe = metrics.get("sharpe", 0.0)
        genome.max_drawdown = metrics.get("max_drawdown", 0.0)
        genome.win_rate = metrics.get("win_rate", 0.0)
        genome.trade_count = metrics.get("trade_count", 0)
        genome.evaluated = True

        # Composite fitness: Sharpe-weighted with drawdown penalty
        genome.fitness = (
            genome.sharpe * 0.5
            + genome.win_rate * 0.2
            - abs(genome.max_drawdown) * 0.2
            + min(genome.trade_count / 100, 0.1)
        )

        return {"genome_id": genome_id, "fitness": round(genome.fitness, 4), **metrics}

    async def evolve(self) -> dict:
        """Run one generation of evolution: select, crossover, mutate."""
        evaluated = [g for g in self._population if g.evaluated]
        if len(evaluated) < 4:
            return {
                "error": f"Need at least 4 evaluated genomes, have {len(evaluated)}. "
                "Run backtests first with evaluate()."
            }

        self._generation += 1
        evaluated.sort(key=lambda g: g.fitness, reverse=True)

        # Elitism — preserve top performers
        new_pop: list[StrategyGenome] = []
        for g in evaluated[:self.elite_count]:
            elite = StrategyGenome(
                name=f"elite_gen{self._generation}_{len(new_pop)}",
                parent_ids=[g.id],
                generation=self._generation,
                genes=dict(g.genes),
            )
            new_pop.append(elite)

        # Fill rest via crossover + mutation
        while len(new_pop) < self.pop_size:
            if random.random() < self.crossover_rate:
                p1 = self._tournament_select(evaluated)
                p2 = self._tournament_select(evaluated)
                child_genes = self._crossover(p1.genes, p2.genes)
                child = StrategyGenome(
                    name=f"child_gen{self._generation}_{len(new_pop)}",
                    parent_ids=[p1.id, p2.id],
                    generation=self._generation,
                    genes=child_genes,
                )
            else:
                parent = self._tournament_select(evaluated)
                child = StrategyGenome(
                    name=f"clone_gen{self._generation}_{len(new_pop)}",
                    parent_ids=[parent.id],
                    generation=self._generation,
                    genes=dict(parent.genes),
                )

            self._mutate(child)
            new_pop.append(child)

        best = evaluated[0]
        gen_record = {
            "generation": self._generation,
            "population_size": len(new_pop),
            "best_fitness": round(best.fitness, 4),
            "best_sharpe": round(best.sharpe, 2),
            "best_genome": best.id,
            "avg_fitness": round(sum(g.fitness for g in evaluated) / len(evaluated), 4),
            "timestamp": time.time(),
        }
        self._history.append(gen_record)
        self._population = new_pop

        logger.info(
            "Generation %d: best_fitness=%.4f best_sharpe=%.2f pop=%d",
            self._generation, best.fitness, best.sharpe, len(new_pop),
        )
        return gen_record

    async def get_top(self, n: int = 10) -> list[dict]:
        """Get top N genomes by fitness."""
        evaluated = sorted(
            [g for g in self._population if g.evaluated],
            key=lambda g: g.fitness, reverse=True,
        )
        return [g.to_dict() for g in evaluated[:n]]

    async def get_unevaluated(self, n: int = 10) -> list[dict]:
        """Get N genomes that still need backtesting."""
        unevaluated = [g for g in self._population if not g.evaluated]
        return [g.to_dict() for g in unevaluated[:n]]

    async def get_history(self) -> list[dict]:
        """Get evolution history across all generations."""
        return list(self._history)

    async def get_genome(self, genome_id: str) -> Optional[dict]:
        """Get a specific genome by ID."""
        g = self._find_genome(genome_id)
        return g.to_dict() if g else None

    # ── Genetic operators ─────────────────────────────────────────

    def _tournament_select(self, pool: list[StrategyGenome], k: int = 3) -> StrategyGenome:
        """Tournament selection: pick k random, return best."""
        candidates = random.sample(pool, min(k, len(pool)))
        return max(candidates, key=lambda g: g.fitness)

    def _crossover(self, genes_a: dict, genes_b: dict) -> dict:
        """Uniform crossover: each gene randomly from parent A or B."""
        child = {}
        for key in genes_a:
            if key in genes_b:
                child[key] = genes_a[key] if random.random() < 0.5 else genes_b[key]
            else:
                child[key] = genes_a[key]
        return child

    def _mutate(self, genome: StrategyGenome) -> None:
        """Gaussian mutation: perturb each gene with probability mutation_rate."""
        for gene_name in list(genome.genes.keys()):
            if random.random() < self.mutation_rate:
                lo, hi = GENE_RANGES.get(gene_name, (0, 1))
                current = genome.genes[gene_name]
                sigma = (hi - lo) * 0.1
                mutated = current + random.gauss(0, sigma)
                genome.genes[gene_name] = max(lo, min(hi, mutated))

    def _random_gene(self, name: str) -> float:
        lo, hi = GENE_RANGES.get(name, (0, 1))
        return random.uniform(lo, hi)

    def _find_genome(self, genome_id: str) -> Optional[StrategyGenome]:
        for g in self._population:
            if g.id == genome_id:
                return g
        return None
