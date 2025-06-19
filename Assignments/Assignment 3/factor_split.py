
"""
A Python module to find two factors, a and b, of a number N 
such that their absolute difference |a-b| is minimized.

This module provides two primary functions:
1.  split_factors_iterative: A simple, fast method for N up to ~10^16.
2.  factor_based_split: A robust method for arbitrarily large N using
    prime factorization. It requires the 'sympy' library.
"""

import math
import bisect

# It's good practice to handle optional dependencies gracefully.
try:
    from sympy import factorint
    _SYMPY_AVAILABLE = True
except ImportError:
    _SYMPY_AVAILABLE = False


def split_factors_iterative(n: int) -> tuple[int, int]:
    """
    Finds two factors, a and b, of n such that |a-b| is minimized.
    This method iterates downwards from sqrt(n) and is very fast for n 
    up to around 10**16.

    Args:
        n: A positive integer.

    Returns:
        A tuple (a, b) where a * b = n, a <= b, and b - a is minimized.
    """
    if not isinstance(n, int) or n <= 0:
        raise ValueError("Input must be a positive integer.")
    if n == 1:
        return (1, 1)

    # Start checking from the integer part of the square root of n
    a = int(math.sqrt(n))

    # Iterate downwards until a divisor is found
    while a > 0:
        if n % a == 0:
            b = n // a
            return (a, b)
        a -= 1
    
    # This part is theoretically unreachable for n > 1 since 1 is always a factor.
    return (1, n)


def factor_based_split(n: int) -> tuple[int, int]:
    """
    Finds two factors, a and b, of n by first computing the prime factorization.
    This method is asymptotically faster for very large n. It requires SymPy.

    Args:
        n: A positive integer.

    Returns:
        A tuple (a, b) where a * b = n, a <= b, and b - a is minimized.
        
    Raises:
        ImportError: If the 'sympy' library is not installed.
    """
    if not _SYMPY_AVAILABLE:
        raise ImportError("The 'factor_based_split' function requires the SymPy library. Please install it using: pip install sympy")

    if not isinstance(n, int) or n <= 0:
        raise ValueError("Input must be a positive integer.")
    if n == 1:
        return (1, 1)

    # 1. Get the prime factorization of n
    prime_factors_dict = factorint(n)
    factors = []
    for p, exponent in prime_factors_dict.items():
        factors.extend([p] * exponent)

    # Helper to generate all sub-products from a list of factors
    def get_sub_products(factor_list):
        products = {1}
        for factor in factor_list:
            products.update({p * factor for p in products})
        return sorted(list(products))

    target = math.sqrt(n)
    mid = len(factors) // 2
    
    products1 = get_sub_products(factors[:mid])
    products2 = get_sub_products(factors[mid:])

    best_a = 1
    for p1 in products1:
        if p1 > target:
            break
            
        required_p2 = target / p1
        idx = bisect.bisect_right(products2, required_p2)
        if idx > 0:
            p2 = products2[idx - 1]
            current_a = p1 * p2
            if current_a > best_a:
                best_a = current_a

    a = best_a
    b = n // a
    return (a, b)

# This block allows the file to be run directly to show examples
if __name__ == '__main__':
    print("--- Testing factor_split.py ---")

    print("\nMethod 1: Iterative Approach")
    print(f"N = 120: {split_factors_iterative(120)}")
    print(f"N = 119: {split_factors_iterative(119)}")
    large_n1 = 9999999999999995
    print(f"N = {large_n1}: {split_factors_iterative(large_n1)}")
    
    print("\nMethod 2: Factorization-based Approach")
    if _SYMPY_AVAILABLE:
        print(f"N = 88200: {factor_based_split(88200)}")
        large_n2 = 123456789101112131415161718192021
        print(f"N = {large_n2}:")
        factors = factor_based_split(large_n2)
        print(f"  a = {factors[0]}")
        print(f"  b = {factors[1]}")
    else:
        print("Skipping factor_based_split tests: SymPy library not found.")
