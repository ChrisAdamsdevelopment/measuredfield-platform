import copy
import pytest
from app.services import takeoff_proposals as proposals, takeoff_share as share
from .test_takeoff_share import packet


def manual_packet():
    p=packet()
    p["field_entered_quantities"]=[{"id":"field_1","scope_item_id":"scope_1","assembly_id":"tile_floor",
        "quantity":15,"unit":"square feet","source":"field_entered_unverified"}]
    return p


def test_draft_is_source_linked_not_applied():
    p=manual_packet()
    r=proposals._proposal(p,"field_entered_quantities","field_1","field_quantity",20,"Verify revised field dimension")
    assert r["expected_quantity"]==15 and r["proposed_quantity"]==20
    assert r["status"]=="draft_only" and p["field_entered_quantities"][0]["quantity"]==15
    assert proposals._proposal(p,"measurements","m1","review_note",None,"Recheck scale")["status"]=="draft_only"


@pytest.mark.parametrize("section,target,kind,quantity",[
    ("measurements","m1","field_quantity",10),
    ("field_entered_quantities","missing","field_quantity",20),
    ("field_entered_quantities","field_1","field_quantity",15),
    ("field_entered_quantities","field_1","field_quantity",-1),
    ("field_entered_quantities","field_1","field_quantity",True),
    ("field_entered_quantities","field_1","field_quantity",float("inf")),
    ("field_entered_quantities","field_1","field_quantity",1000001),
    ("material_lines","line_1","review_note",1),
    ("material_lines","line_1","price_override",10),
])
def test_draft_rejects_invalid_changes(section,target,kind,quantity):
    with pytest.raises(share.ShareError):
        proposals._proposal(manual_packet(),section,target,kind,quantity,"review")


def test_count_is_integer_and_duplicate_sources_fail_closed():
    p=manual_packet()
    p["field_entered_quantities"][0].update(unit="items")
    with pytest.raises(share.ShareError):
        proposals._proposal(p,"field_entered_quantities","field_1","field_quantity",1.5,"review")
    p["field_entered_quantities"].append(copy.deepcopy(p["field_entered_quantities"][0]))
    with pytest.raises(share.ShareError):
        proposals._proposal(p,"field_entered_quantities","field_1","field_quantity",2,"review")
