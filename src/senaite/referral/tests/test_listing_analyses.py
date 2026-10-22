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

    def __init__(self, uid, request, technical_uid="", source_uid=""):
        self.uid = uid
        self.request = request
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

    def _append_html_element(self, item, key, html):
        self.appended.append((key, html))


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
            listing, "get_image", lambda name, title="": title
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
