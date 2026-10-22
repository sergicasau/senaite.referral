# -*- coding: utf-8 -*-

import unittest

from senaite.referral import selective


class Stub(object):

    def __init__(self, uid, request=None, technical_uid=""):
        self.uid = uid
        self.request = request
        self.ReferralTechnicalAnalysisUID = technical_uid
        self.reindexed = []

    def getAnalyses(self, **kwargs):
        return self.analyses

    def getAnalysisService(self):
        return object()

    def getRequest(self):
        return self.request

    def reindexObject(self, idxs=None):
        self.reindexed.append(idxs)


class SelectiveReferralTest(unittest.TestCase):

    def test_partition_assignment_state_is_refreshed(self):
        sample = Stub("sample")
        sample.isRootAncestor = lambda: True
        partition = Stub("partition")
        laboratory = Stub("laboratory")
        canonical = Stub("canonical", sample)
        technical = Stub("technical", partition)

        originals = {
            "IAnalysisRequest": selective.IAnalysisRequest,
            "get_object": selective.api.get_object,
            "get_review_status": selective.api.get_review_status,
            "validate_external_analysis_states": (
                selective.validate_external_analysis_states
            ),
            "group_external_analyses": selective.group_external_analyses,
            "get_available_partition": selective.get_available_partition,
            "create_technical_analysis": selective._create_technical_analysis,
            "set_correlation": selective.set_correlation,
        }
        try:
            selective.IAnalysisRequest = type(
                "AnalysisRequestInterface",
                (),
                {"providedBy": staticmethod(lambda obj: True)},
            )
            selective.api.get_object = lambda obj: obj
            selective.api.get_review_status = lambda obj: "sample_received"
            selective.validate_external_analysis_states = lambda obj: None
            selective.group_external_analyses = lambda obj: {
                "BCN": {
                    "laboratory": laboratory,
                    "analyses": [canonical],
                }
            }
            selective.get_available_partition = lambda obj, lab: partition
            selective._create_technical_analysis = (
                lambda obj, analysis: technical
            )
            selective.set_correlation = lambda *args: None

            prepared = selective.prepare_external_analyses(sample, object())

            self.assertEqual([partition], prepared)
            self.assertEqual([["assigned_state"]], partition.reindexed)
        finally:
            selective.IAnalysisRequest = originals["IAnalysisRequest"]
            selective.api.get_object = originals["get_object"]
            selective.api.get_review_status = originals["get_review_status"]
            selective.validate_external_analysis_states = originals[
                "validate_external_analysis_states"
            ]
            selective.group_external_analyses = originals[
                "group_external_analyses"
            ]
            selective.get_available_partition = originals[
                "get_available_partition"
            ]
            selective._create_technical_analysis = originals[
                "create_technical_analysis"
            ]
            selective.set_correlation = originals["set_correlation"]

    def test_technical_copies_are_not_prepared_again(self):
        sample = Stub("sample")
        partition = Stub("partition")
        canonical = Stub("canonical", sample, technical_uid="technical")
        technical = Stub("technical", partition)
        sample.analyses = [canonical, technical]
        laboratory = Stub("laboratory")

        get_uid = selective.api.get_uid
        get_field_value = selective.get_field_value
        get_referral_mode = selective.get_referral_mode
        validate_external_service = selective.validate_external_service
        try:
            selective.api.get_uid = lambda obj: obj.uid
            selective.get_field_value = (
                lambda obj, name, default=None: getattr(obj, name, default)
            )
            selective.get_referral_mode = lambda service: selective.MODE_EXTERNAL
            selective.validate_external_service = lambda service: laboratory

            groups = selective.group_external_analyses(sample)
            self.assertEqual([], groups.values())
        finally:
            selective.api.get_uid = get_uid
            selective.get_field_value = get_field_value
            selective.get_referral_mode = get_referral_mode
            selective.validate_external_service = validate_external_service


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        SelectiveReferralTest
    )


if __name__ == "__main__":
    unittest.main(argv=["test_selective"])
