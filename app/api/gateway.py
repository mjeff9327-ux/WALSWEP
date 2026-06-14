import time
import logging
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.components.scan_engine import ScanEngine
from app.components.license_service import LicenseService
from app.components.event_bus import EventBus
from app.components.config_manager import ConfigManager
from app.components.affiliate_splitter import AffiliateSplitter
from app.components.token_scanner import TokenScanner
from app.interfaces.node_client import INodeClient

logger = logging.getLogger(__name__)


class ScanRequest(BaseModel):
    seeds: list[str]
    chains: list[str] | None = None


class JobStatus(BaseModel):
    job_id: str
    status: str
    scanned: int = 0
    found: int = 0


class AffiliateSplitRequest(BaseModel):
    amount: float
    chain: str
    source_address: str
    fee: float = 0.0
    usd_value: float = 0.0


class TokenScanRequest(BaseModel):
    address: str
    chain: str


async def create_app(
    scan_engine: ScanEngine,
    license_service: LicenseService,
    event_bus: EventBus,
    config: Optional[ConfigManager] = None,
    node_client: Optional[INodeClient] = None,
) -> FastAPI:
    app = FastAPI(title="Mnemonic Hunter API", version="1.0.0")
    jobs: dict[str, dict] = {}

    splitter = AffiliateSplitter(config) if config else None
    scanner = TokenScanner(node_client) if node_client else None

    @app.post("/api/v1/scan")
    async def enqueue_scan(req: ScanRequest):
        job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        jobs[job_id] = {"status": "running", "seeds": req.seeds, "scanned": 0, "found": 0}
        if req.chains:
            scan_engine.set_chains(req.chains)
        count = 0
        found = 0
        for seed in req.seeds:
            result = await scan_engine.scan_single(seed)
            count += 1
            if result.found:
                found += 1
        jobs[job_id] = {"status": "completed", "scanned": count, "found": found}
        return {"job_id": job_id, "scanned": count, "found": found}

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return JobStatus(job_id=job_id, **job)

    @app.get("/api/v1/events")
    async def list_events():
        return {"message": "Events are recorded locally in events/ directory"}

    @app.post("/api/v1/license/generate")
    async def generate_license(license_key: str):
        token = license_service.generate(license_key)
        if not token:
            raise HTTPException(status_code=400, detail="Invalid license key")
        return {"token": token, "features": ["scan", "multi_chain", "export"]}

    @app.post("/api/v1/affiliate/split")
    async def affiliate_split(req: AffiliateSplitRequest):
        if not splitter:
            raise HTTPException(status_code=400, detail="Affiliate splitter not initialized")
        proposals = splitter.split(req.amount, req.chain, req.source_address, req.fee, req.usd_value)
        if not proposals:
            raise HTTPException(status_code=400, detail="Affiliate split not configured")
        return {
            "proposals": [
                {
                    "amount": p.amount,
                    "destination": p.destination,
                    "chain": p.chain,
                    "usd_value": p.usd_value,
                }
                for p in proposals
            ],
            "total_amount": req.amount,
            "chain": req.chain,
        }

    @app.get("/api/v1/affiliate/config")
    async def affiliate_config():
        if not config:
            raise HTTPException(status_code=400, detail="Config not available")
        return config.get_section("affiliate")

    @app.post("/api/v1/affiliate/config")
    async def update_affiliate_config(settings: dict):
        if not config:
            raise HTTPException(status_code=400, detail="Config not available")
        current = config.data
        if "affiliate" not in current:
            current["affiliate"] = {}
        current["affiliate"].update(settings)
        return current["affiliate"]

    @app.post("/api/v1/scan/tokens")
    async def scan_tokens(req: TokenScanRequest):
        if not scanner:
            raise HTTPException(status_code=400, detail="Token scanner not initialized")
        tokens = await scanner.scan(req.address, req.chain)
        return {
            "address": req.address,
            "chain": req.chain,
            "tokens": tokens,
            "count": len(tokens),
        }

    @app.get("/api/v1/tokens/supported")
    async def supported_tokens():
        from app.components.token_scanner import ERC20_CONTRACTS, TRC20_CONTRACTS
        result = {}
        for chain, contracts in ERC20_CONTRACTS.items():
            result[chain] = [{"symbol": t["symbol"], "contract": t["contract"]} for t in contracts]
        result["TRON"] = [{"symbol": t["symbol"], "contract": t["contract"]} for t in TRC20_CONTRACTS]
        return result

    return app
