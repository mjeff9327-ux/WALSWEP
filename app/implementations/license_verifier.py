import hashlib
import hmac
import time

from app.interfaces.license_verifier import ILicenseVerifier, Entitlement


class ProductionLicenseVerifier(ILicenseVerifier):
    def __init__(self, secret_key: str = ""):
        self._secret_key = secret_key

    def verify(self, key: str) -> Entitlement:
        if not key:
            return Entitlement(valid=False, features=[])

        if not self._secret_key:
            return Entitlement(valid=False, features=[])

        parts = key.split("-")
        if len(parts) != 3:
            return Entitlement(valid=False, features=[])

        payload = parts[0]
        provided_mac = parts[1]
        expiry_str = parts[2]

        expected_mac = hmac.new(
            self._secret_key.encode(),
            f"{payload}:{expiry_str}".encode(),
            hashlib.sha256,
        ).hexdigest()[:12]

        if not hmac.compare_digest(provided_mac, expected_mac):
            return Entitlement(valid=False, features=[])

        try:
            expiry = int(expiry_str, 16)
            if time.time() > expiry:
                return Entitlement(valid=False, features=[])
        except ValueError:
            return Entitlement(valid=False, features=[])

        feature_map = {
            "s": "scan",
            "m": "multi_chain",
            "e": "export",
            "w": "auto_withdraw",
        }
        features = []
        for ch in payload:
            if ch in feature_map:
                features.append(feature_map[ch])

        if "scan" not in features:
            return Entitlement(valid=False, features=[])

        return Entitlement(
            valid=True,
            features=features,
            expires_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(expiry)),
        )
