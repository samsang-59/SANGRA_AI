from fastapi import APIRouter

from app.controllers import health_controller

router = APIRouter(tags=["health"])

# Public.
router.add_api_route("/health", health_controller.get_health, methods=["GET"])
