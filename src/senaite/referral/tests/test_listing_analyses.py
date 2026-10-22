# -*- coding: utf-8 -*-

import unittest

import senaite.referral
from senaite.referral.adapters.listing import analyses as listing
from zope.interface import implementer

from bika.lims.interfaces import IAnalysisRequest
from bika.lims.interfaces.analysis import IRequestAnalysis


@implementer(IAnalysisRequest)
class SampleStub(object):

    def __init__(self, uid, shipped=False):
        self.uid = uid
        self.shipped = shipped

    def hasOutboundShipment(self):
        return self.shipped


@implementer(IRequestAnalysis)
class AnalysisStub(object):

    def __init__(self, uid, request, technical_uid="", source_uid="",
                 review_state="unassigned"):
        self.uid = uid
        self.request = request
        self.review_state = review_state
        self.ReferralTechnicalAnalysisUID = technical_uid
        self.ReferralSourceAnalysisUID = source_uid
        self.analysts = []
        self.verifiers = []
        self.instrument = ""
        self.method = ""

    def getRequest(self):
        return self.request

    def getReferenceAnalysts(self):
        return self.analysts

    def getReferenceVerifiers(self):
        return self.verifiers

    def getReferenceInstrument(self):
        return self.instrument

    def getReferenceMethod(self):
        return self.method


class ListingStub(object):

    def __init__(self, uids=None, review_state_filter=None, total=0):
        self.contentFilter = {}
        if uids is not None:
            self.contentFilter["getAncestorsUIDs"] = uids
        self.review_state = {
            "id": "default",
            "contentFilter": review_state_filter or {},
        }
        self.total = total
        self.show_methodinstr_columns = False
        self.appended = []

    def get_object(self, obj):
        return obj

    def _append_html_element(self, item, key, html, glue="&nbsp;"):
        # same behavior as AnalysesView._append_html_element from core
        self.appended.append((key, html))
        item["after"] = item.get("after", {})
        original = item["after"].get(key, "")
        item["after"][key] = glue.join([original, html]) if original else html


class SampleAnalysesListingAdapterTest(unittest.TestCase):

    def setUp(self):
        self.primary = SampleStub("primary")
        self.partition = SampleStub("partition")

        self._patch(senaite.referral, "is_installed", lambda: True)
        self._patch(listing.api, "get_uid", lambda obj: obj.uid)
        self._patch(
            listing,
            "get_field_value",
            lambda obj, name, default=None: getattr(obj, name, default),
        )
        self._patch(listing, "t", lambda msg: msg)
        self._patch(
            listing,
            "get_image",
            lambda name, title="": '<img src="{}" title="{}" />'.format(
                name, title
            ),
        )
        self._patch(
            listing.api, "get_review_status", lambda obj: obj.review_state
        )

    def _patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def _adapter(self, context, **kwargs):
        view = ListingStub(**kwargs)
        return listing.SampleAnalysesListingAdapter(view, context), view

    def _canonical(self, uid="tas1"):
        analysis = AnalysisStub(
            uid, self.primary, technical_uid="technical-" + uid
        )
        analysis.analysts = [{"fullname": "Jane", "lab_code": "BCN"}]
        analysis.verifiers = [{"fullname": "John", "lab_code": "BCN"}]
        analysis.instrument = "ICP"
        analysis.method = "EPA"
        return analysis

    def _technical(self, uid="technical-tas1"):
        return AnalysisStub(uid, self.partition, source_uid="tas1")

    def _verified(self, analysis, state="verified"):
        analysis.review_state = state
        return analysis

    def _item_with_core_warning(self, *others):
        """Item as the core listing leaves it for an analysis submitted and
        verified by the same user: the warning, followed by other elements
        """
        warning = (
            '<img src="warning.png" title="Submitted and verified by the '
            'same user: mad_lab" />'
        )
        item = self._item()
        item["after"] = {"state_title": "&nbsp;".join((warning,) + others)}
        return item

    @staticmethod
    def _remote_indicator(verifiers="John (BCN)"):
        return (
            '<img src="warning.png" title="Verified by reference lab: '
            '{}" />'.format(verifiers)
        )

    def _item(self):
        return {"replace": {}, "Service": "TAS"}

    def test_canonical_delegated_analysis_is_referred(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        self.assertTrue(adapter.is_referred(self._canonical()))

    def test_canonical_delegated_analysis_displays_remote_actors(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        item = self._item()

        adapter.folder_item(self._canonical(), item, 0)

        self.assertEqual("Jane (BCN)", item["replace"]["Analyst"])
        self.assertEqual(" ", item["replace"]["SubmittedBy"])
        self.assertEqual("ICP", item["replace"]["Instrument"])
        self.assertEqual("EPA", item["replace"]["Method"])
        self.assertTrue(view.show_methodinstr_columns)
        self.assertEqual(1, len(view.appended))

    def test_technical_analysis_is_referred(self):
        adapter, view = self._adapter(self.partition, uids=["partition"])
        self.assertTrue(adapter.is_referred(self._technical()))

    def test_local_analysis_is_not_referred(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        local = AnalysisStub("tas3", self.primary)
        item = self._item()

        self.assertFalse(adapter.is_referred(local))
        self.assertFalse(adapter.is_hidden_technical(local))
        adapter.folder_item(local, item, 0)

        self.assertEqual({"replace": {}, "Service": "TAS"}, item)
        self.assertEqual([], view.appended)

    def test_legacy_full_sample_referral_is_preserved(self):
        self.primary.shipped = True
        adapter, view = self._adapter(self.primary, uids=["primary"])
        legacy = AnalysisStub("tas9", self.primary)
        legacy.analysts = [{"fullname": "Jane", "lab_code": "BCN"}]
        item = self._item()

        self.assertTrue(adapter.is_referred(legacy))
        self.assertFalse(adapter.is_hidden_technical(legacy))
        adapter.folder_item(legacy, item, 0)

        self.assertEqual("Jane (BCN)", item["replace"]["Analyst"])

    def test_technical_analysis_is_hidden_from_primary_default_view(self):
        adapter, view = self._adapter(
            self.primary, uids=["primary"], total=6
        )
        item = self._item()

        adapter.folder_item(self._technical(), item, 0)

        # the listing dismisses items that are cleared by subscribers
        self.assertFalse(item)
        self.assertEqual(5, view.total)

    def test_technical_analysis_is_hidden_from_primary_root_filter(self):
        # the filter of the root sample is set as a single uid, not a list
        adapter, view = self._adapter(
            self.primary,
            uids=["primary"],
            review_state_filter={"getAncestorsUIDs": "primary"},
        )
        item = self._item()

        adapter.folder_item(self._technical(), item, 0)

        self.assertFalse(item)

    def test_technical_analysis_is_visible_in_partition_filter(self):
        # the filter (tab) of the partition inside the primary sample view
        adapter, view = self._adapter(
            self.primary,
            uids=["primary"],
            review_state_filter={"getAncestorsUIDs": "partition"},
            total=2,
        )
        item = self._item()

        adapter.folder_item(self._technical(), item, 0)

        self.assertTrue(item)
        self.assertEqual(2, view.total)

    def test_technical_analysis_is_visible_in_partition_view(self):
        adapter, view = self._adapter(
            self.partition, uids=["partition"], total=2
        )
        item = self._item()

        adapter.folder_item(self._technical(), item, 0)

        self.assertTrue(item)
        self.assertEqual(2, view.total)

    def test_technical_analysis_is_visible_when_partition_is_context(self):
        # even if the listing has no ancestors filter at all
        adapter, view = self._adapter(self.partition)
        self.assertFalse(adapter.is_hidden_technical(self._technical()))

    def test_total_is_never_negative(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])

        adapter.folder_item(self._technical(), self._item(), 0)

        self.assertEqual(0, view.total)

    def test_verified_referred_analysis_replaces_local_warning(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        item = self._item_with_core_warning()

        adapter.folder_item(self._verified(self._canonical()), item, 0)

        # only the remote verification indicator remains in the status cell
        self.assertEqual(
            {"state_title": self._remote_indicator()}, item["after"]
        )
        self.assertNotIn("mad_lab", item["after"]["state_title"])
        # the remote analyst is displayed and the local submitter is blank
        self.assertEqual("Jane (BCN)", item["replace"]["Analyst"])
        self.assertEqual(" ", item["replace"]["SubmittedBy"])

    def test_published_referred_analysis_replaces_local_warning(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        item = self._item_with_core_warning()

        analysis = self._verified(self._canonical(), "published")
        adapter.folder_item(analysis, item, 0)

        self.assertEqual(
            {"state_title": self._remote_indicator()}, item["after"]
        )

    def test_technical_copy_in_partition_replaces_local_warning(self):
        adapter, view = self._adapter(self.partition, uids=["partition"])
        item = self._item_with_core_warning()
        technical = self._verified(self._technical())
        technical.analysts = [{"fullname": "Jane", "lab_code": "BCN"}]
        technical.verifiers = [{"fullname": "John", "lab_code": "BCN"}]

        adapter.folder_item(technical, item, 0)

        self.assertEqual(
            {"state_title": self._remote_indicator()}, item["after"]
        )
        self.assertEqual("Jane (BCN)", item["replace"]["Analyst"])
        self.assertEqual(" ", item["replace"]["SubmittedBy"])

    def test_local_analysis_keeps_core_warning(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        item = self._item_with_core_warning()
        before = dict(item["after"])

        local = self._verified(AnalysisStub("tas3", self.primary))
        adapter.folder_item(local, item, 0)

        self.assertEqual(before, item["after"])
        self.assertEqual([], view.appended)

    def test_referred_without_remote_verifiers_keeps_state_content(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        item = self._item_with_core_warning()
        before = dict(item["after"])
        canonical = self._verified(self._canonical())
        canonical.verifiers = []

        adapter.folder_item(canonical, item, 0)

        self.assertEqual(before, item["after"])
        self.assertEqual([], view.appended)
        # the remote analyst is still displayed
        self.assertEqual("Jane (BCN)", item["replace"]["Analyst"])

    def test_not_yet_verified_referred_analysis_keeps_core_warning(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        item = self._item_with_core_warning()
        warning = item["after"]["state_title"]

        canonical = self._verified(self._canonical(), "to_be_verified")
        adapter.folder_item(canonical, item, 0)

        self.assertEqual(
            "&nbsp;".join([warning, self._remote_indicator()]),
            item["after"]["state_title"],
        )

    def test_other_content_of_the_state_cell_is_preserved(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        counter = "<a href='#' class='multi-verification'>1/2</a>"
        worksheet = (
            '<a href="/ws"><img src="worksheet.png" title="WS-1" /></a>'
        )
        item = self._item_with_core_warning(counter, worksheet)

        adapter.folder_item(self._verified(self._canonical()), item, 0)

        self.assertEqual(
            "&nbsp;".join([counter, worksheet, self._remote_indicator()]),
            item["after"]["state_title"],
        )

    def test_unexpected_state_markup_is_left_untouched(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        # not glued with the glue used by core, so it cannot be told apart
        content = '<img src="warning.png" title="x" /> <a href="#">y</a>'
        item = self._item()
        item["after"] = {"state_title": content}

        adapter.folder_item(self._verified(self._canonical()), item, 0)

        self.assertEqual(
            "&nbsp;".join([content, self._remote_indicator()]),
            item["after"]["state_title"],
        )

    def test_remove_state_icons_drops_the_empty_cell(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        item = self._item_with_core_warning()

        adapter.remove_state_icons(item)

        self.assertEqual({}, item["after"])

    def test_remove_state_icons_without_state_cell(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        for item in (self._item(), {"after": {}}, {"after": None}):
            adapter.remove_state_icons(item)

    def test_reference_actors_are_returned_as_a_list(self):
        adapter, view = self._adapter(self.primary, uids=["primary"])
        self.assertEqual([], adapter.get_reference_actors([]))
        self.assertEqual(
            ["Jane (BCN)"],
            adapter.get_reference_actors(
                [{"fullname": "Jane", "lab_code": "BCN"}]
            ),
        )

    def test_adapter_does_nothing_if_not_installed(self):
        self._patch(senaite.referral, "is_installed", lambda: False)
        adapter, view = self._adapter(self.primary, uids=["primary"], total=6)
        item = self._item()

        adapter.folder_item(self._technical(), item, 0)

        self.assertEqual({"replace": {}, "Service": "TAS"}, item)
        self.assertEqual(6, view.total)


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        SampleAnalysesListingAdapterTest
    )


if __name__ == "__main__":
    unittest.main(argv=["test_listing_analyses"])
