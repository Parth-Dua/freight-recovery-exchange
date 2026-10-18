"""Recovery allocation (Tisya's lane): check carrier claims, solve with OR-Tools, explain.

`SolverAllocationSeam` is the real implementation of `freight_seams.AllocationSeam`.
"""

from freight_allocation.capacity import CapacityReader, StaticCapacity, TigerCapacityReader
from freight_allocation.explain import explain, money
from freight_allocation.model import AllocationInputError, Solution, ineligibility, solve
from freight_allocation.seam import SolverAllocationSeam, input_version
from freight_allocation.verify import ClaimCheck, check_claims

__all__ = [
    "AllocationInputError",
    "CapacityReader",
    "ClaimCheck",
    "Solution",
    "SolverAllocationSeam",
    "StaticCapacity",
    "TigerCapacityReader",
    "check_claims",
    "explain",
    "ineligibility",
    "input_version",
    "money",
    "solve",
]
