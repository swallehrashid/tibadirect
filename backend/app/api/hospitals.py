"""The hospital directory a family chooses from."""

from flask.views import MethodView

from app.api.base import TibaBlueprint
from app.extensions import db
from app.pagination import PaginationArgsSchema, page_of, paginate
from app.schemas.hospitals import HospitalListItemSchema, HospitalQuerySchema
from app.services import hospital_service

blp = TibaBlueprint("hospitals", __name__, url_prefix="/api/hospitals", description="Hospitals")


@blp.route("")
class Hospitals(MethodView):
    @blp.arguments(HospitalQuerySchema, location="query")
    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(HospitalListItemSchema))
    def get(self, filters: dict, paging: dict) -> dict:
        """Active hospitals by name, for the case wizard. ``?query=`` narrows by name or county."""
        return paginate(db.session, hospital_service.active_hospitals(filters["query"]), paging)
