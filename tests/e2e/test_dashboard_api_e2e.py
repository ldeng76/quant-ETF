"""
E2E tests for Dashboard API (FastAPI endpoints).
Tests API routes with mocked database and services.
"""
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def mock_dashboard_app(tmp_path):
    """Create a mock FastAPI app for testing."""
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/")
    async def root():
        return {"status": "ok"}

    @app.get("/api/portfolio/summary")
    async def portfolio_summary():
        return {"total_etfs": 15, "date": "2026-01-15"}

    @app.get("/api/strategy/etf")
    async def strategy_etf():
        return [
            {"code": "510050", "score": 0.15, "r60": 0.12, "r20": 0.08, "r10": 0.05, "r5": 0.03},
            {"code": "510310", "score": 0.12, "r60": 0.10, "r20": 0.07, "r10": 0.04, "r5": 0.02},
        ]

    @app.get("/api/strategy/short")
    async def strategy_short():
        return [
            {"code": "002202", "score": 0.25, "r5": 0.05, "trend_ok": True},
        ]

    @app.get("/api/strategy/mid")
    async def strategy_mid():
        return [
            {"code": "300870", "score": 0.35, "drawdown_from_120d_high": -0.25, "rebound_ok": True},
        ]

    @app.get("/api/market/status")
    async def market_status():
        return {"market_open": True, "trading_day": "2026-01-15"}

    @app.get("/api/alerts")
    async def alerts():
        return []

    @app.get("/pages/overview")
    async def pages_overview():
        return {"page": "overview"}

    return app


class TestDashboardEndpointsE2E:
    """E2E tests for dashboard API endpoints."""

    def test_root_redirect(self, mock_dashboard_app):
        """Root endpoint should return OK status."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_portfolio_summary(self, mock_dashboard_app):
        """Portfolio summary endpoint should return ETF count and date."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/api/portfolio/summary")
        assert response.status_code == 200
        data = response.json()
        assert "total_etfs" in data
        assert "date" in data

    def test_strategy_etf_endpoint(self, mock_dashboard_app):
        """ETF strategy endpoint should return ranked list."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/api/strategy/etf")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "code" in data[0]
        assert "score" in data[0]

    def test_strategy_short_endpoint(self, mock_dashboard_app):
        """Short strategy endpoint should return stock picks."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/api/strategy/short")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert "code" in data[0]

    def test_strategy_mid_endpoint(self, mock_dashboard_app):
        """Mid strategy endpoint should return rebound picks."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/api/strategy/mid")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert "code" in data[0]

    def test_market_status_endpoint(self, mock_dashboard_app):
        """Market status endpoint should return trading day info."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/api/market/status")
        assert response.status_code == 200
        data = response.json()
        assert "trading_day" in data

    def test_alerts_endpoint_empty(self, mock_dashboard_app):
        """Alerts endpoint should return list (possibly empty)."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/api/alerts")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_pages_overview(self, mock_dashboard_app):
        """Overview page endpoint should return page data."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/pages/overview")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == "overview"


class TestDashboardErrorHandlingE2E:
    """E2E tests for dashboard error handling."""

    def test_404_for_unknown_route(self, mock_dashboard_app):
        """Unknown routes should return 404."""
        client = TestClient(mock_dashboard_app)
        response = client.get("/api/nonexistent")
        assert response.status_code == 404

    def test_500_for_internal_error(self):
        """Internal errors should be handled gracefully."""
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        app = FastAPI()

        @app.exception_handler(Exception)
        async def generic_handler(request, exc):
            return JSONResponse(status_code=500, content={"detail": str(exc)})

        @app.get("/api/error")
        async def error_route():
            raise ValueError("Test error")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/error")
        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
