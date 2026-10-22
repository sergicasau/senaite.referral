# -*- coding: utf-8 -*-

import unittest
from datetime import datetime

import pytz
from DateTime import DateTime
from senaite.referral import remotelab
from senaite.referral.jsonapi import outboundsample
from senaite.referral.remote import resource as remote_resource

# Real capture time at the remote laboratory (Barcelona, summer time)
CAPTURED = "2026-10-22T09:41:36+02:00"

ANALYSTS = [{"fullname": "Jane Doe", "lab_code": "BCN"}]
VERIFIERS = [{"fullname": "John Roe", "lab_code": "BCN"}]


class ResultFieldStub(object):

    def set(self, analysis, value):
        analysis.values["Result"] = value


class AnalysisStub(object):
    """Analysis that records the values written by the result receiver"""

    def __init__(self, uid, keyword, request, review_state, delegation_id="",
                 source_uid="", technical_uid=""):
        self.uid = uid
        self.keyword = keyword
        self.request = request
        self.review_state = review_state
        self.ReferralDelegationID = delegation_id
        self.ReferralSourceAnalysisUID = source_uid
        self.ReferralTechnicalAnalysisUID = technical_uid
        self.values = {}

    def __getattr__(self, name):
        # setters (setResult, setReferenceAnalysts...) store what they receive
        if name.startswith("set") and "values" in self.__dict__:
            return lambda value: self.values.__setitem__(name[3:], value)
        raise AttributeError(name)

    def getRequest(self):
        return self.request

    def getField(self, name):
        return ResultFieldStub()

    def reindexObject(self, idxs=None):
        pass


class RemoteAnalysisStub(object):
    """Analysis of the remote laboratory that provides the capture dates"""

    def __init__(self, captured=None, submitted=None):
        self.captured = captured
        self.submitted = submitted

    def getResultCaptureDate(self):
        return self.captured

    def getDateSubmitted(self):
        return self.submitted


class SampleStub(object):

    def __init__(self, uid, review_state):
        self.uid = uid
        self.review_state = review_state
        self.analyses = []

    def getAnalyses(self, **kwargs):
        return self.analyses


class LaboratoryStub(object):

    def __init__(self, code):
        self.code = code

    def getCode(self):
        return self.code


class CaptureDateSerializationTest(unittest.TestCase):
    """The result capture date is sent with its time and UTC offset"""

    def test_time_and_utc_offset_are_kept(self):
        analysis = RemoteAnalysisStub(captured=DateTime(CAPTURED))
        self.assertEqual(CAPTURED, remotelab.get_result_capture_date(analysis))

    def test_submission_date_is_the_fallback(self):
        analysis = RemoteAnalysisStub(submitted=DateTime(CAPTURED))
        self.assertEqual(CAPTURED, remotelab.get_result_capture_date(analysis))

    def test_capture_date_has_priority_over_submission_date(self):
        analysis = RemoteAnalysisStub(
            captured=DateTime(CAPTURED),
            submitted=DateTime("2026-10-23T18:00:00+02:00"),
        )
        self.assertEqual(CAPTURED, remotelab.get_result_capture_date(analysis))

    def test_no_dates_result_in_an_empty_string(self):
        analysis = RemoteAnalysisStub()
        self.assertEqual("", remotelab.get_result_capture_date(analysis))

    def test_python_datetime_is_serialized_without_fraction(self):
        # the fraction would break parsers that expect "YYYY-MM-DD HH:MM:SS.f"
        value = datetime(2026, 10, 22, 9, 41, 36, 789000,
                         tzinfo=pytz.FixedOffset(120))
        self.assertEqual(CAPTURED, remotelab.to_iso8601(value))


class SelectiveResultsTest(unittest.TestCase):
    """Results from the remote lab go to the canonical analyses and to their
    technical copies, and only to them
    """

    def setUp(self):
        self.primary = SampleStub("primary", "sample_received")
        self.partition = SampleStub("partition", "received_at_reference")
        self.laboratory = LaboratoryStub("BCN")
        self.objects = {"primary": self.primary, "partition": self.partition}

        # business analyses of the primary sample: two external, one local
        self.tas1, self.tech_tas1 = self._delegated("tas1")
        self.tas2, self.tech_tas2 = self._delegated("tas2")
        self.tas3 = self._add(
            AnalysisStub("tas3", "TAS3", self.primary, "unassigned"),
            self.primary,
        )
        # unrelated analysis that happens to share the keyword with TAS1
        self.other = self._add(
            AnalysisStub("other", "TAS1", self.primary, "unassigned"),
            self.primary,
        )

        self._patch(
            outboundsample.api, "get_review_status", lambda o: o.review_state
        )
        self._patch(outboundsample.api, "get_uid", lambda o: o.uid)
        self._patch(outboundsample.api, "get_object", self._get_object)
        self._patch(outboundsample, "link_remote_resource", lambda *a: None)
        self._patch(outboundsample, "unlink_remote_resource", lambda *a: None)
        self._patch(
            outboundsample, "get_create_reference_analyses", lambda: False
        )
        self._patch(
            outboundsample,
            "get_object_by_remote_uid",
            lambda uid, default=None: default,
        )
        self._patch(
            remote_resource,
            "get_object_by_remote_uid",
            lambda uid, default=None: default,
        )
        self._patch(
            outboundsample,
            "is_referral_partition",
            lambda obj: obj is self.partition,
        )
        self._patch(
            outboundsample,
            "get_partition_laboratory",
            lambda obj: self.laboratory,
        )
        self._patch(
            outboundsample, "get_primary_sample", lambda obj: self.primary
        )
        self._patch(
            outboundsample,
            "get_field_value",
            lambda obj, name, default=None: getattr(obj, name, default),
        )
        self._patch(outboundsample, "alsoProvides", lambda *args: None)
        self._patch(outboundsample, "changeWorkflowState", self._change_state)
        self._patch(outboundsample, "doActionFor", self._do_action)

    def _patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def _get_object(self, uid, default=None):
        return self.objects.get(uid, default)

    def _change_state(self, obj, workflow, state, **kwargs):
        obj.review_state = state

    def _do_action(self, obj, action):
        if action == "verify" and obj.review_state == "to_be_verified":
            obj.review_state = "verified"
        return True, ""

    def _add(self, analysis, sample):
        sample.analyses.append(analysis)
        self.objects[analysis.uid] = analysis
        return analysis

    def _delegated(self, uid):
        """Returns the canonical analysis and its technical copy"""
        keyword = uid.upper()
        delegation = "delegation-" + uid
        canonical = AnalysisStub(
            uid, keyword, self.primary, "referred",
            delegation_id=delegation, technical_uid="technical-" + uid,
        )
        technical = AnalysisStub(
            "technical-" + uid, keyword, self.partition, "unassigned",
            delegation_id=delegation, source_uid=uid,
        )
        self._add(canonical, self.primary)
        self._add(technical, self.partition)
        return canonical, technical

    def _record(self, canonical, result="12.5 mg/L", date=CAPTURED,
                delegation_id=None):
        if delegation_id is None:
            delegation_id = canonical.ReferralDelegationID
        return {
            "id": "remote-" + canonical.uid,
            "uid": "remote-uid-" + canonical.uid,
            "keyword": canonical.keyword,
            "formatted_result": result,
            "delegation_id": delegation_id,
            "source_analysis_uid": canonical.uid,
            "is_retest": False,
            "result_date": date,
            "instrument": "ICP-MS",
            "method": "EPA 200.8",
            "analysts": ANALYSTS,
            "verifiers": VERIFIERS,
        }

    def _process(self, *records):
        data = {
            "consumer": "senaite.referral.outbound_sample",
            "lab_code": "BCN",
            "sample": {
                "id": "remote-partition",
                "uid": "remote-partition-uid",
                "referring_id": "AA00023-P01",
                "shipment_id": "SH-0001",
                "selective_referral": True,
                "origin_partition_uid": "partition",
                "analyses": list(records),
            },
        }
        return outboundsample.OutboundSampleConsumer(data).process()

    def test_canonical_and_technical_analyses_receive_the_same_result(self):
        self._process(
            self._record(self.tas1, result="12.5 mg/L"),
            self._record(self.tas2, result="0.8 mg/L"),
        )

        for canonical, technical, result in (
                (self.tas1, self.tech_tas1, "12.5 mg/L"),
                (self.tas2, self.tech_tas2, "0.8 mg/L")):
            self.assertEqual(result, canonical.values["Result"])
            self.assertEqual(result, technical.values["Result"])
            self.assertEqual(
                canonical.values["ResultCaptureDate"].ISO8601(),
                technical.values["ResultCaptureDate"].ISO8601(),
            )

    def test_both_analyses_reach_the_verified_state(self):
        self._process(
            self._record(self.tas1),
            self._record(self.tas2),
        )

        for analysis in (self.tas1, self.tech_tas1,
                         self.tas2, self.tech_tas2):
            self.assertEqual("verified", analysis.review_state)

    def test_reference_actors_are_stored_in_the_canonical_analysis(self):
        self._process(self._record(self.tas1))

        self.assertEqual(ANALYSTS, self.tas1.values["ReferenceAnalysts"])
        self.assertEqual(VERIFIERS, self.tas1.values["ReferenceVerifiers"])
        self.assertEqual("ICP-MS", self.tas1.values["ReferenceInstrument"])
        self.assertEqual("EPA 200.8", self.tas1.values["ReferenceMethod"])

    def test_capture_timestamp_survives_the_round_trip(self):
        # what the remote laboratory sends...
        remote = RemoteAnalysisStub(captured=DateTime(CAPTURED))
        sent = remotelab.get_result_capture_date(remote)

        # ...is what both analyses of the origin laboratory store
        self._process(self._record(self.tas1, date=sent))

        expected = DateTime(CAPTURED)
        for analysis in (self.tas1, self.tech_tas1):
            captured = analysis.values["ResultCaptureDate"]
            self.assertEqual(CAPTURED, captured.ISO8601())
            self.assertEqual(expected.timeTime(), captured.timeTime())
            # neither midnight nor the 02:00 produced by the date-only format
            self.assertEqual((9, 41), (captured.hour(), captured.minute()))

    def test_date_only_timestamp_from_older_remote_is_accepted(self):
        self._process(self._record(self.tas1, date="2026-10-22"))

        captured = self.tas1.values["ResultCaptureDate"]
        self.assertEqual("2026-10-22", captured.ISO8601()[:10])

    def test_analyses_without_correlation_are_not_updated(self):
        # TAS1 was delegated, but there is another analysis with that keyword,
        # as well as local analyses and other technical copies
        self._process(self._record(self.tas1))

        self.assertEqual({}, self.other.values)
        self.assertEqual("unassigned", self.other.review_state)
        self.assertEqual({}, self.tas3.values)
        self.assertEqual({}, self.tech_tas2.values)
        self.assertEqual({}, self.tas2.values)

    def test_mismatching_delegation_updates_nothing(self):
        record = self._record(self.tas1, delegation_id="delegation-forged")

        self.assertRaises(ValueError, self._process, record)

        for analysis in (self.tas1, self.tech_tas1, self.other):
            self.assertEqual({}, analysis.values)


def test_suite():
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(CaptureDateSerializationTest))
    suite.addTests(loader.loadTestsFromTestCase(SelectiveResultsTest))
    return suite


if __name__ == "__main__":
    unittest.main(argv=["test_selective_results"])
