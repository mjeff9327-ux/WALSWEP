import logging
import secrets

logger = logging.getLogger(__name__)

FIELD_PRIME = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F


class MpcOperator:
    def __init__(self, prime: int = FIELD_PRIME):
        self._prime = prime

    def _mod(self, value: int) -> int:
        return value % self._prime

    def _eval_polynomial(self, coeffs: list[int], x: int) -> int:
        result = 0
        for coeff in reversed(coeffs):
            result = self._mod(result * x + coeff)
        return result

    def _lagrange_interpolate(self, shares: list[tuple[int, int]], x: int) -> int:
        result = 0
        for i, (xi, yi) in enumerate(shares):
            numerator = 1
            denominator = 1
            for j, (xj, _yj) in enumerate(shares):
                if i == j:
                    continue
                numerator = self._mod(numerator * (x - xj))
                denominator = self._mod(denominator * (xi - xj))
            denom_inv = pow(denominator, -1, self._prime)
            li = self._mod(numerator * denom_inv)
            result = self._mod(result + yi * li)
        return result

    def share_secret(self, secret: int, num_shares: int, threshold: int) -> list[tuple[int, int]]:
        if threshold > num_shares:
            raise ValueError("Threshold cannot exceed number of shares")
        if threshold < 2:
            raise ValueError("Threshold must be at least 2")
        if secret >= self._prime or secret < 0:
            raise ValueError(f"Secret must be in range [0, {self._prime - 1}]")

        coeffs = [secret] + [secrets.randbelow(self._prime - 1) + 1 for _ in range(threshold - 1)]
        shares = [(i + 1, self._eval_polynomial(coeffs, i + 1)) for i in range(num_shares)]
        return shares

    def reconstruct_secret(self, shares: list[tuple[int, int]]) -> int:
        if len(shares) < 2:
            raise ValueError("Need at least 2 shares for reconstruction")
        return self._lagrange_interpolate(shares, 0)

    def threshold_signing_op(self) -> dict:
        secret = secrets.randbelow(self._prime - 1) + 1
        threshold = 2
        total_shares = 3
        shares = self.share_secret(secret, total_shares, threshold)
        reconstructed = self.reconstruct_secret(shares[:threshold])
        match = reconstructed == secret

        return {
            "type": "Shamir's Secret Sharing — 2-of-3 threshold scheme",
            "secret_hex": hex(secret),
            "threshold": threshold,
            "total_shares": total_shares,
            "shares": [{"id": sid, "value": val} for sid, val in shares],
            "reconstructed_from_first_n": threshold,
            "reconstructed_hex": hex(reconstructed),
            "reconstruction_match": match,
            "note": (
                "MPC wallets split the private key into multiple shares using "
                "Shamir's Secret Sharing. An attacker must compromise at least "
                "'threshold' number of shares to reconstruct the key. "
                "Fewer shares reveals zero information about the secret."
            ),
        }
