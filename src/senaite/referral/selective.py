# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.REFERRAL.

import uuid
from collections import OrderedDict

from bika.lims import api
from bika.lims.interfaces import IAnalysisRequest
from bika.lims.utils import changeWorkflowState
from bika.lims.utils.analysis import create_analysis
from bika.lims.utils.analysisrequest import create_partition
from senaite.core.workflow import ANALYSIS_WORKFLOW
from senaite.referral.interfaces import IExternalLaboratory
from senaite.referral.utils import get_field_value
from senaite.referral.utils import get_lab_code
from senaite.referral.utils import set_field_value


MODE_INTERNAL = "internal"
MODE_EXTERNAL = "external"


def get_referral_mode(service):
    """Return the configured execution mode for an Analysis Service."""
    return get_field_value(service, "ReferralMode", MODE_INTERNAL)


def get_executing_laboratory_code(service):
    """Return the stable code of the laboratory that executes the service."""
    return get_field_value(service, "ExecutingLaboratoryCode", "") or ""


def get_reference_laboratory(service):
    """Return the destination External Laboratory for a service, if any."""
    return get_field_value(service, "ReferenceLaboratory", None)


def validate_external_service(service):
    """Validate and return the destination lab for an external service."""
    if get_referral_mode(service) != MODE_EXTERNAL:
        return None

    laboratory = get_reference_laboratory(service)
    laboratory = api.get_object(laboratory, default=None)
    keyword = service.getKeyword()
    if not IExternalLaboratory.providedBy(laboratory):
        raise ValueError(
            "External service '{}' has no reference laboratory".format(
                keyword
            )
        )
    if not api.is_active(laboratory) or not laboratory.getReference():
        raise ValueError(
            "Reference laboratory for service '{}' is not active or is not "
            "enabled as a reference laboratory".format(keyword)
        )

    executing_code = get_executing_laboratory_code(service)
    origin_code = get_lab_code()
    if not origin_code:
        raise ValueError(
            "The current SENAITE instance has no referral laboratory code"
        )
    if not executing_code:
        raise ValueError(
            "External service '{}' has no executing laboratory code".format(
                keyword
            )
        )
    if executing_code != laboratory.getCode():
        raise ValueError(
            "Executing laboratory code '{}' for service '{}' does not match "
            "reference laboratory code '{}'".format(
                executing_code, keyword, laboratory.getCode()
            )
        )
    if executing_code == origin_code:
        raise ValueError(
            "External service '{}' points to the current laboratory"
            .format(keyword)
        )
    return laboratory


def validate_local_service(service):
    """Validate that a delegated service is local in this instance."""
    if get_referral_mode(service) != MODE_INTERNAL:
        raise ValueError(
            "Delegated service '{}' is not configured as Internal in the "
            "executing laboratory".format(service.getKeyword())
        )

    code = get_executing_laboratory_code(service)
    current_code = get_lab_code()
    if not current_code:
        raise ValueError(
            "The current SENAITE instance has no referral laboratory code"
        )
    if not code:
        raise ValueError(
            "Internal service '{}' has no executing laboratory code".format(
                service.getKeyword()
            )
        )
    if code != current_code:
        raise ValueError(
            "Executing laboratory code '{}' for service '{}' does not match "
            "this instance code '{}'".format(
                code, service.getKeyword(), current_code
            )
        )


def is_referral_partition(sample):
    """Return whether the sample is a technical selective-referral aliquot."""
    if not IAnalysisRequest.providedBy(sample):
        return False
    return bool(get_field_value(sample, "ReferralPartition", False))


def get_primary_sample(sample):
    """Return the client-facing primary sample for a referral partition."""
    sample = api.get_object(sample)
    if not is_referral_partition(sample):
        return sample
    primary = sample.getParentAnalysisRequest()
    return api.get_object(primary, default=sample)


def get_partition_laboratory(sample):
    """Return the destination lab configured for a referral partition."""
    laboratory = get_field_value(sample, "ReferralLaboratory", None)
    return api.get_object(laboratory, default=None)


def make_delegation_id(analysis, laboratory):
    """Build a deterministic identifier for one delegated analysis."""
    seed = "{}:{}:{}".format(
        get_lab_code() or "",
        api.get_uid(analysis),
        laboratory.getCode(),
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def group_external_analyses(sample):
    """Return unassigned external analyses grouped by destination lab UID."""
    groups = OrderedDict()
    sample_uid = api.get_uid(sample)
    analyses = sample.getAnalyses(
        full_objects=True,
        review_state="unassigned",
        sort_on="sortable_title",
        sort_order="ascending",
    )
    for analysis in analyses:
        if api.get_uid(analysis.getRequest()) != sample_uid:
            continue
        if get_field_value(analysis, "ReferralTechnicalAnalysisUID", ""):
            continue
        service = analysis.getAnalysisService()
        if get_referral_mode(service) != MODE_EXTERNAL:
            continue
        laboratory = validate_external_service(service)
        uid = api.get_uid(laboratory)
        group = groups.setdefault(uid, {
            "laboratory": laboratory,
            "analyses": [],
        })
        group["analyses"].append(analysis)
    return groups


def validate_external_analysis_states(sample):
    """Prevent delegating analyses that are already assigned locally."""
    analyses = sample.getAnalyses(
        full_objects=True,
        review_state="assigned",
    )
    assigned = []
    for analysis in analyses:
        service = analysis.getAnalysisService()
        if get_referral_mode(service) == MODE_EXTERNAL:
            assigned.append(analysis.getKeyword())
    if assigned:
        raise ValueError(
            "External analyses must be unassigned before preparation: {}"
            .format(", ".join(assigned))
        )


def set_correlation(canonical, technical, laboratory):
    """Link canonical and transport analyses using a stable delegation id."""
    delegation_id = make_delegation_id(canonical, laboratory)
    set_field_value(canonical, "ReferralDelegationID", delegation_id)
    set_field_value(
        canonical, "ReferralOriginLabCode", get_lab_code() or ""
    )
    set_field_value(
        canonical, "ReferralTechnicalAnalysisUID", api.get_uid(technical)
    )
    set_field_value(technical, "ReferralDelegationID", delegation_id)
    set_field_value(
        technical, "ReferralSourceAnalysisUID", api.get_uid(canonical)
    )
    set_field_value(
        technical, "ReferralOriginLabCode", get_lab_code() or ""
    )
    canonical.reindexObject()
    technical.reindexObject()
    return delegation_id


def get_available_partition(primary, laboratory):
    """Return an unshipped referral partition for the destination lab."""
    laboratory_uid = api.get_uid(laboratory)
    for partition in primary.getDescendants(all_descendants=False):
        if not is_referral_partition(partition):
            continue
        if api.get_uid(get_partition_laboratory(partition)) != laboratory_uid:
            continue
        if partition.getOutboundShipment():
            continue
        if api.get_review_status(partition) != "sample_received":
            continue
        return partition
    return None


def mark_referral_partition(partition, primary, laboratory):
    """Mark an aliquot as an internal, non-invoiceable transport sample."""
    set_field_value(partition, "ReferralPartition", True)
    set_field_value(partition, "ReferralLaboratory", api.get_uid(laboratory))
    set_field_value(partition, "ReferralOriginLabCode", get_lab_code() or "")
    set_field_value(partition, "ReferralPrimaryUID", api.get_uid(primary))
    if hasattr(partition, "setInternalUse"):
        partition.setInternalUse(True)
    if hasattr(partition, "setInvoiceExclude"):
        partition.setInvoiceExclude(True)
    partition.reindexObject()


def _create_technical_analysis(partition, canonical):
    """Add a technical analysis to an existing received partition.

    ``create_analysis`` copies the fields of the canonical analysis, which is
    not internal use, so the technical copy has to be marked explicitly.
    Marking only the partition is not enough: reports filter analyses
    individually. This applies to both new and reused partitions.
    """
    technical = create_analysis(partition, canonical)
    technical.setInternalUse(True)
    if api.get_review_status(technical) != "unassigned":
        changeWorkflowState(
            technical,
            ANALYSIS_WORKFLOW,
            "unassigned",
            action="initialize",
        )
    technical.reindexObject()
    return technical


def prepare_external_analyses(sample, request=None):
    """Create one technical referral aliquot per destination laboratory.

    Canonical analyses stay on the primary sample. The returned partitions
    contain copies used only for transport and remote workflow tracking.
    Calling this function again is safe: already correlated analyses are
    ignored and an existing unshipped partition is reused.
    """
    sample = api.get_object(sample)
    if not IAnalysisRequest.providedBy(sample):
        raise ValueError("Only samples can be prepared for referral")
    if not sample.isRootAncestor():
        raise ValueError("Prepare referrals from the primary sample")
    if api.get_review_status(sample) != "sample_received":
        raise ValueError("The primary sample must be received first")

    request = request or api.get_request()
    validate_external_analysis_states(sample)
    groups = group_external_analyses(sample)
    prepared = []
    for group in groups.values():
        laboratory = group["laboratory"]
        canonical_analyses = group["analyses"]
        partition = get_available_partition(sample, laboratory)

        if not partition:
            partition = create_partition(
                sample,
                request,
                [],
                internal_use=True,
            )
            mark_referral_partition(partition, sample, laboratory)

        technical_analyses = [
            _create_technical_analysis(partition, analysis)
            for analysis in canonical_analyses
        ]
        matches = zip(canonical_analyses, technical_analyses)

        for canonical, technical in matches:
            set_correlation(canonical, technical, laboratory)

        partition.reindexObject(idxs=["assigned_state"])
        prepared.append(partition)

    return prepared
