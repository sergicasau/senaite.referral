# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.REFERRAL.
#
# SENAITE.REFERRAL is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2021-2022 by it's authors.
# Some rights reserved, see README and LICENSE.

import re

from senaite.app.listing.interfaces import IListingView
from senaite.app.listing.interfaces import IListingViewAdapter
from senaite.referral import check_installed
from senaite.referral import messageFactory as _
from senaite.referral.utils import get_field_value
from zope.component import adapter
from zope.interface import implementer

from bika.lims import api
from bika.lims.interfaces import IAnalysisRequest
from bika.lims.interfaces.analysis import IRequestAnalysis
from bika.lims.utils import get_image
from bika.lims.utils import t


# Statuses in which the verification of an analysis is completed
VERIFIED_STATES = ("verified", "published")

# Glue used by the listing to concatenate the elements of a cell. See
# `AnalysesView._append_html_element` from senaite.core
CELL_GLUE = "&nbsp;"

# A standalone icon, e.g. `<img src="warning.svg" title="..." />`. Anchors
# (multi-verification counter, link to worksheet, etc.) are not matched
STANDALONE_ICON = re.compile(r"^\s*<img\b[^<>]*>\s*$")


@adapter(IListingView)
@implementer(IListingViewAdapter)
class SampleAnalysesListingAdapter(object):
    """Adapter for analyses listings
    """

    # Priority order of this adapter over others
    priority_order = 99999

    def __init__(self, listing, context):
        self.listing = listing
        self.context = context

    @check_installed(None)
    def before_render(self):
        # Modify "Valid" filter to include "referred" analyses
        for rv in self.listing.review_states:
            if rv["id"] != "default":
                continue

            if "referred" in rv["contentFilter"]["review_state"]:
                continue

            rv["contentFilter"]["review_state"].append("referred")

    @check_installed(None)
    def folder_item(self, obj, item, index):
        analysis = self.listing.get_object(obj)
        if self.is_hidden_technical(analysis):
            # Dismiss the row: the listing drops items cleared by subscribers
            # and keeps "total" in sync with the rows actually displayed
            item.clear()
            self.listing.total = max(self.listing.total - 1, 0)
            return item

        if self.is_referred(analysis):
            # Display empty in "Analyst" and "Submitter" columns
            item["replace"].update({
                "Analyst": " ",
                "SubmittedBy": " ",
            })

            # Display the reference lab verifiers instead of local
            verifiers = self.get_reference_verifiers(analysis)
            if verifiers:
                if api.get_review_status(analysis) in VERIFIED_STATES:
                    # Core warns about "submitted and verified by the same
                    # user" from the local workflow history, but both
                    # transitions were replayed by the integration account
                    # when the remote result was imported. The reference lab
                    # verifiers are the reliable source, so the icon below
                    # replaces the misleading one from core
                    self.remove_state_icons(item)

                msg = t(_("Verified by reference lab: {}"))
                verifiers = ", ".join(verifiers)
                icon = get_image('warning.png', title=msg.format(verifiers))
                self.listing._append_html_element(item, 'state_title', icon)

            # Display the reference lab analysts instead of local
            analysts = self.get_reference_analysts(analysis)
            item["replace"]["Analyst"] = ", ".join(analysts)

            # Display the instrument of the ref lab
            instrument = analysis.getReferenceInstrument() or ""
            item["replace"]["Instrument"] = instrument

            # Display the method of the ref lab
            method = analysis.getReferenceMethod() or ""
            item["replace"]["Method"] = method

            if any([instrument, method]):
                # Tell the listing to display the instrument/method columns
                self.listing.show_methodinstr_columns = True

        return item

    def remove_state_icons(self, item):
        """Removes the standalone icons added by core to the status cell of
        the item, but preserves any other content, such as the link to the
        worksheet or the multi-verification counter
        """
        state_after = item.get("after") or {}
        html = state_after.get("state_title")
        if not html:
            return

        # elements that are not a single icon (or whose glue is not the one
        # we expect) are kept as they are
        parts = html.split(CELL_GLUE)
        parts = [part for part in parts if not STANDALONE_ICON.match(part)]
        if parts:
            state_after["state_title"] = CELL_GLUE.join(parts)
        else:
            state_after.pop("state_title", None)

    def get_reference_analysts(self, analysis):
        """Returns a list with the names of the reference verifiers for the
        given analysis, with the reference laboratory code appended
        """
        analysts = analysis.getReferenceAnalysts()
        return self.get_reference_actors(analysts)

    def get_reference_verifiers(self, analysis):
        """Returns a list with the names of the reference verifiers for the
        given analysis, with the reference laboratory code appended
        """
        verifiers = analysis.getReferenceVerifiers()
        return self.get_reference_actors(verifiers)

    def get_reference_actors(self, actors):
        """Returns a list with the names of the actors, with the reference
        laboratory code appended
        """
        actors = ["{} ({})".format(ref.get("fullname"), ref.get("lab_code"))
                  for ref in actors]
        return [actor for actor in actors if actor]

    def is_referred(self, thing):
        """Returns whether the obj has been referred to another laboratory
        """
        if IAnalysisRequest.providedBy(thing):
            return thing.hasOutboundShipment()

        elif IRequestAnalysis.providedBy(thing):
            # Canonical analysis delegated through a selective referral
            if self.is_delegated(thing):
                return True

            # Technical copy that lives in the referral partition
            if self.is_technical(thing):
                return True

            sample = thing.getRequest()
            return self.is_referred(sample)

    def is_delegated(self, analysis):
        """Returns whether the analysis is a canonical analysis for which a
        technical copy was created for the reference laboratory
        """
        return bool(get_field_value(
            analysis, "ReferralTechnicalAnalysisUID", ""))

    def is_technical(self, analysis):
        """Returns whether the analysis is a technical copy, used only to
        transport the analysis to the reference laboratory
        """
        return bool(get_field_value(
            analysis, "ReferralSourceAnalysisUID", ""))

    def is_hidden_technical(self, analysis):
        """Returns whether the analysis is a technical copy that must not be
        displayed in the current listing.

        Technical copies are displayed only when the listing is explicitly
        scoped to the partition they belong to: either the partition is the
        context of the listing or its filter (tab) has been selected. The
        default view of the primary sample, which includes the analyses from
        its partitions, displays the canonical analyses only.
        """
        if not self.is_technical(analysis):
            return False

        partition = analysis.getRequest()
        if not partition:
            return False

        partition_uid = api.get_uid(partition)
        if partition_uid == api.get_uid(self.context):
            return False

        return partition_uid not in self.get_scoped_ancestors()

    def get_scoped_ancestors(self):
        """Returns the UIDs of the samples the listing is currently filtered
        by, as done by the listing when the catalog query is built: the
        filters of the active review state take precedence over the ones set
        in the listing itself
        """
        query = dict(self.listing.contentFilter)
        query.update(self.listing.review_state.get("contentFilter", {}))
        uids = query.get("getAncestorsUIDs") or []
        if not isinstance(uids, (list, tuple, set)):
            uids = [uids]
        return list(uids)


@adapter(IListingView)
@implementer(IListingViewAdapter)
class SampleManageAnalysesListingAdapter(object):
    """Adapter for manage analyses listings
    """

    # Priority order of this adapter over others
    priority_order = 99999

    _referring_services = None

    def __init__(self, listing, context):
        self.listing = listing
        self.context = context

    @property
    def referring_services(self):
        """Returns the UIDs of the services initially requested by the
        referring laboratory, if any
        """
        if self._referring_services is None:
            self._referring_services = []
            inbound_sample = self.context.getInboundSample()
            if inbound_sample:
                self._referring_services = inbound_sample.getRawServices()
        return self._referring_services

    @check_installed(None)
    def before_render(self):
        pass

    @check_installed(None)
    def folder_item(self, obj, item, index):
        # Skip services without analyses in the sample
        uid = api.get_uid(obj)
        if uid not in self.listing.analyses:
            return item

        if uid in self.referring_services:
            # Mark the row as disabled as it was requested by referring lab
            item["disabled"] = True

        return item
