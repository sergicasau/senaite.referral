# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.REFERRAL.

from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

from bika.lims import api
from senaite.referral import messageFactory as _
from senaite.referral.browser import BaseView
from senaite.referral.selective import group_external_analyses
from senaite.referral.selective import prepare_external_analyses


class PrepareExternalAnalysesView(BaseView):
    """Preview and create the technical aliquots for external analyses."""

    template = ViewPageTemplateFile(
        "templates/prepare_external_analyses.pt"
    )

    def __call__(self):
        form = self.request.form
        if form.get("submitted") and form.get("button_cancel"):
            return self.redirect()

        if form.get("submitted") and form.get("button_prepare"):
            try:
                partitions = prepare_external_analyses(
                    self.context, request=self.request
                )
            except (TypeError, ValueError) as error:
                return self.reload(message=str(error), level="error")

            if not partitions:
                return self.redirect(
                    message=_("No external analyses require preparation"),
                    level="warning",
                )

            ids = ", ".join(map(api.get_id, partitions))
            return self.redirect(
                message=_(
                    "Referral aliquots prepared successfully: {}".format(ids)
                )
            )

        return self.template()

    def get_groups(self):
        """Return template-friendly destination/analysis records."""
        groups = []
        for group in group_external_analyses(self.context).values():
            groups.append({
                "laboratory": api.get_title(group["laboratory"]),
                "code": group["laboratory"].getCode(),
                "analyses": [
                    {
                        "keyword": analysis.getKeyword(),
                        "title": api.get_title(analysis),
                    }
                    for analysis in group["analyses"]
                ],
            })
        return groups
