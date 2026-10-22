# -*- coding: utf-8 -*-

import unittest

from senaite.referral import selective


class Stub(object):

    def __init__(self, uid, request=None, technical_uid=""):
        self.uid = uid
        self.request = request
        self.ReferralTechnicalAnalysisUID = technical_uid

    def getAnalyses(self, **kwargs):
        return self.analyses

    def getAnalysisService(self):
        return object()

    def getRequest(self):
        return self.request


class SelectiveReferralTest(unittest.TestCase):

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
