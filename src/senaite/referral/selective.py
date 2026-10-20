# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.REFERRAL.

import uuid
from collections import OrderedDict

from bika.lims import api
from bika.lims.interfaces import IAnalysisRequest
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
    if code and current_code and code != current_code:
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
    analyses = sample.getAnalyses(
        full_objects=True,
        review_state=["unassigned", "assigned"],
        sort_on="sortable_title",
        sort_order="ascending",
    )
    for analysis in analyses:
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


def set_correlation(canonical, technical, laboratory):
    """Link canonical and transport analyses using a stable delegation id."""
    delegation_id = make_delegation_id(canonical, laboratory)
    set_field_value(canonical, "ReferralDelegationID", delegation_id)
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
