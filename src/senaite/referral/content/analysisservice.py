# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.REFERRAL.

from archetypes.schemaextender.interfaces import IBrowserLayerAwareExtender
from archetypes.schemaextender.interfaces import ISchemaExtender
from Products.Archetypes.public import DisplayList
from Products.Archetypes.Widget import SelectionWidget
from Products.Archetypes.Widget import StringWidget
from senaite.core.browser.widgets import ReferenceWidget
from senaite.referral import ISenaiteReferralLayer
from senaite.referral import messageFactory as _
from senaite.referral.fields import ExtStringField
from senaite.referral.fields import ExtUIDReferenceField
from zope.component import adapter
from zope.interface import implementer

from bika.lims.interfaces import IAnalysisService


REFERRAL_MODES = DisplayList((
    ("internal", _("Internal (local laboratory)")),
    ("external", _("External (another laboratory)")),
))


@adapter(IAnalysisService)
@implementer(ISchemaExtender, IBrowserLayerAwareExtender)
class AnalysisServiceSchemaExtender(object):
    """Adds the laboratory routing configuration to Analysis Services."""

    layer = ISenaiteReferralLayer
    custom_fields = [
        ExtStringField(
            "ReferralMode",
            default="internal",
            vocabulary=REFERRAL_MODES,
            schemata="Referral",
            widget=SelectionWidget(
                format="select",
                label=_("Execution mode"),
                description=_(
                    "Whether this service is performed in this SENAITE "
                    "instance or referred to another laboratory."
                ),
                render_own_label=True,
            ),
        ),
        ExtStringField(
            "ExecutingLaboratoryCode",
            default="",
            schemata="Referral",
            widget=StringWidget(
                label=_("Executing laboratory code"),
                description=_(
                    "Stable code of the laboratory that performs this "
                    "service. It must be identical in every instance."
                ),
                render_own_label=True,
            ),
        ),
        ExtUIDReferenceField(
            "ReferenceLaboratory",
            allowed_types=("ExternalLaboratory",),
            required=False,
            schemata="Referral",
            widget=ReferenceWidget(
                label=_("Reference laboratory"),
                description=_(
                    "Destination laboratory used when execution mode is "
                    "External."
                ),
                render_own_label=True,
                catalog_name="portal_catalog",
                base_query={
                    "portal_type": "ExternalLaboratory",
                    "is_active": True,
                    "sort_on": "sortable_title",
                    "sort_order": "ascending",
                },
            ),
        ),
    ]

    def __init__(self, context):
        self.context = context

    def getFields(self):
        return self.custom_fields
