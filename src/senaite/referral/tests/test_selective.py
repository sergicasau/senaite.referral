# -*- coding: utf-8 -*-

import unittest

from senaite.referral import selective
from senaite.referral.workflow import analysisrequest


class Stub(object):

    def __init__(self, uid, request=None, technical_uid="",
                 review_state="unassigned"):
        self.uid = uid
        self.request = request
        self.review_state = review_state
        self.ReferralTechnicalAnalysisUID = technical_uid
        self.ReferralSourceAnalysisUID = ""
        self.internal_use = False
        self.analyses = []
        self.reindexed = []
        self.internal_use_at_reindex = []

    def getAnalyses(self, **kwargs):
        return self.analyses

    def getAnalysisService(self):
        return object()

    def getRequest(self):
        return self.request

    def getInternalUse(self):
        return self.internal_use

    def setInternalUse(self, value):
        self.internal_use = bool(value)

    def reindexObject(self, idxs=None):
        self.reindexed.append(idxs)
        self.internal_use_at_reindex.append(self.internal_use)


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


class TechnicalAnalysisCreationTest(unittest.TestCase):
    """Technical copies must always be internal use, on any creation path"""

    def setUp(self):
        self.sample = Stub("sample", review_state="sample_received")
        self.sample.isRootAncestor = lambda: True
        self.laboratory = Stub("bcn")
        self.partitions = []
        self.created = []
        self.workflow_changes = []

        provided = type(
            "AnalysisRequestInterface",
            (),
            {"providedBy": staticmethod(lambda obj: True)},
        )
        self._patch(selective, "IAnalysisRequest", provided)
        self._patch(selective.api, "get_uid", lambda obj: obj.uid)
        self._patch(selective.api, "get_object", lambda obj: obj)
        self._patch(
            selective.api, "get_review_status", lambda obj: obj.review_state
        )
        self._patch(selective, "get_lab_code", lambda: "MAD")
        self._patch(
            selective,
            "set_field_value",
            lambda obj, name, value: setattr(obj, name, value),
        )
        self._patch(
            selective,
            "get_field_value",
            lambda obj, name, default=None: getattr(obj, name, default),
        )
        self._patch(
            selective,
            "get_referral_mode",
            lambda service: selective.MODE_EXTERNAL,
        )
        self._patch(
            selective,
            "validate_external_service",
            lambda service: self.laboratory,
        )
        self._patch(
            selective, "validate_external_analysis_states", lambda sample: None
        )
        self._patch(selective, "create_partition", self._create_partition)
        self._patch(selective, "create_analysis", self._create_analysis)
        self._patch(selective, "changeWorkflowState", self._change_state)
        self._patch(selective, "set_correlation", self._set_correlation)
        self._patch(
            selective, "get_available_partition", self._get_available_partition
        )

    def _patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def _create_partition(self, sample, request, analyses, internal_use=False,
                          **kwargs):
        # The partition is created empty and already internal use
        partition = Stub(
            "partition-{}".format(len(self.partitions) + 1),
            sample,
            review_state="sample_received",
        )
        partition.internal_use = internal_use
        self.partitions.append(partition)
        return partition

    def _create_analysis(self, container, source, **kwargs):
        # Like the real create_analysis, the copy comes from a canonical
        # analysis that is not internal use
        technical = Stub("technical-" + source.uid, container)
        container.analyses.append(technical)
        self.sample.analyses.append(technical)
        self.created.append(source.uid)
        return technical

    def _change_state(self, obj, workflow, state, **kwargs):
        obj.review_state = state
        self.workflow_changes.append((obj.uid, state))

    def _set_correlation(self, canonical, technical, laboratory):
        canonical.ReferralTechnicalAnalysisUID = technical.uid
        technical.ReferralSourceAnalysisUID = canonical.uid

    def _get_available_partition(self, primary, laboratory):
        return self.partitions[0] if self.partitions else None

    def _add_canonical(self, uid):
        canonical = Stub(uid, self.sample)
        self.sample.analyses.append(canonical)
        return canonical

    def _prepare(self):
        return selective.prepare_external_analyses(self.sample, object())

    def test_new_partition_marks_technical_analysis_as_internal_use(self):
        self._add_canonical("tas1")

        prepared = self._prepare()

        self.assertEqual(1, len(prepared))
        partition = prepared[0]
        self.assertTrue(partition.internal_use)
        self.assertEqual(1, len(partition.analyses))
        technical = partition.analyses[0]
        self.assertTrue(technical.internal_use)
        # the flag must be set before the object is reindexed
        self.assertEqual([True], technical.internal_use_at_reindex)

    def test_reused_partition_marks_new_technical_as_internal_use(self):
        self._add_canonical("tas1")
        partition = self._prepare()[0]
        self._add_canonical("tas2")

        prepared = self._prepare()

        self.assertEqual([partition], prepared)
        self.assertEqual(1, len(self.partitions))
        self.assertEqual(
            ["technical-tas1", "technical-tas2"],
            [analysis.uid for analysis in partition.analyses],
        )
        for technical in partition.analyses:
            self.assertTrue(technical.internal_use)

    def test_repeated_preparation_creates_no_additional_analysis(self):
        self._add_canonical("tas1")
        self._add_canonical("tas2")

        first = self._prepare()
        second = self._prepare()

        self.assertEqual(2, len(first[0].analyses))
        self.assertEqual([], second)
        self.assertEqual(["tas1", "tas2"], self.created)
        self.assertEqual(1, len(self.partitions))

    def test_partition_assignment_state_is_reindexed_on_both_paths(self):
        self._add_canonical("tas1")
        partition = self._prepare()[0]
        self.assertEqual(["assigned_state"], partition.reindexed[-1])

        self._add_canonical("tas2")
        self._prepare()
        self.assertEqual(["assigned_state"], partition.reindexed[-1])
        self.assertEqual(2, partition.reindexed.count(["assigned_state"]))

    def test_technical_analysis_is_initialized_in_unassigned_state(self):
        self._add_canonical("tas1")
        # a copy that is not created as unassigned must be initialized
        original = self._create_analysis

        def create_received(container, source, **kwargs):
            technical = original(container, source, **kwargs)
            technical.review_state = "registered"
            return technical

        self._patch(selective, "create_analysis", create_received)

        technical = self._prepare()[0].analyses[0]

        self.assertEqual(
            [("technical-tas1", "unassigned")], self.workflow_changes
        )
        self.assertEqual("unassigned", technical.review_state)
        self.assertTrue(technical.internal_use)


class SelectiveRetestSynchronizationTest(unittest.TestCase):
    """Invalidated referral partitions must supersede technical analyses"""

    def setUp(self):
        self.primary = Stub("primary")
        self.invalidated = Stub("partition", review_state="invalid")
        self.retest = Stub("partition-retest", review_state="shipped")
        self.laboratory = Stub("bcn")
        self.canonical = Stub("canonical", review_state="retracted")
        self.canonical_retest = Stub("canonical-retest")
        self.technical = Stub(
            "technical", self.invalidated, review_state="verified"
        )
        self.new_technical = Stub("technical-retest", self.retest)

        self.technical.ReferralSourceAnalysisUID = self.canonical.uid
        self.technical.ReferralDelegationID = "delegation-tas1"
        self.technical.getKeyword = lambda: "TAS1"
        self.new_technical.getKeyword = lambda: "TAS1"
        self.invalidated.analyses = [self.technical]
        self.retest.analyses = [self.new_technical]
        self.canonical.getRetest = lambda: self.canonical_retest

        self.retest.setOutboundShipment = lambda value: None
        self.retest.setInvoiceExclude = lambda value: None
        self.retest.getParentAnalysisRequest = lambda: self.primary

        self.workflow_changes = []
        self.provided = []

        self._patch(
            analysisrequest,
            "get_partition_laboratory",
            lambda sample: self.laboratory,
        )
        self._patch(
            analysisrequest,
            "get_field_value",
            lambda obj, name, default=None: getattr(obj, name, default),
        )
        self._patch(
            analysisrequest,
            "set_field_value",
            lambda obj, name, value: setattr(obj, name, value),
        )
        self._patch(
            analysisrequest.api,
            "get_object",
            lambda uid, default=None: (
                self.canonical if uid == self.canonical.uid else default
            ),
        )
        self._patch(analysisrequest.api, "get_uid", lambda obj: obj.uid)
        self._patch(
            analysisrequest.api,
            "get_review_status",
            lambda obj: obj.review_state,
        )
        self._patch(
            analysisrequest, "set_workflow_state", self._change_state
        )
        self._patch(
            analysisrequest,
            "alsoProvides",
            lambda obj, interface: self.provided.append((obj, interface)),
        )

    def _patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def _change_state(self, obj, workflow, state, **kwargs):
        obj.review_state = state
        self.workflow_changes.append((obj.uid, state, kwargs.get("action")))

    def test_old_technical_analysis_is_retracted(self):
        analysisrequest.synchronize_selective_retest(
            self.invalidated, self.retest
        )

        self.assertEqual("retracted", self.technical.review_state)
        self.assertIn(
            ("technical", "retracted", "retract"), self.workflow_changes
        )
        self.assertIn(
            (self.technical, analysisrequest.IRetracted), self.provided
        )
        self.assertEqual(
            self.canonical_retest.uid,
            self.new_technical.ReferralSourceAnalysisUID,
        )

    def test_retraction_is_idempotent(self):
        analysisrequest.synchronize_selective_retest(
            self.invalidated, self.retest
        )
        analysisrequest.synchronize_selective_retest(
            self.invalidated, self.retest
        )

        transitions = [
            change for change in self.workflow_changes
            if change[0] == self.technical.uid
        ]
        self.assertEqual(
            [("technical", "retracted", "retract")], transitions
        )

    def test_primary_descendants_are_reindexed(self):
        analysisrequest.synchronize_selective_retest(
            self.invalidated, self.retest
        )

        self.assertEqual(
            [["getDescendantsUIDs"]], self.primary.reindexed
        )


def test_suite():
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(SelectiveReferralTest))
    suite.addTests(loader.loadTestsFromTestCase(TechnicalAnalysisCreationTest))
    suite.addTests(
        loader.loadTestsFromTestCase(SelectiveRetestSynchronizationTest)
    )
    return suite


if __name__ == "__main__":
    unittest.main(argv=["test_selective"])
