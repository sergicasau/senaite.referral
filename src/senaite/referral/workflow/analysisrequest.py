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

from bika.lims import api
from bika.lims.interfaces import IRejected
from bika.lims.interfaces import IRetracted
from bika.lims.utils import changeWorkflowState as set_workflow_state
from bika.lims.utils.analysis import create_retest as create_analysis_retest
from bika.lims.workflow import doActionFor
from bika.lims.workflow import isTransitionAllowed
from senaite.core.workflow import ANALYSIS_WORKFLOW
from senaite.core.workflow import SAMPLE_WORKFLOW
from senaite.referral import check_installed
from senaite.referral.interfaces import IInboundSampleShipment
from senaite.referral.interfaces import IOutboundSampleShipment
from senaite.referral.remotelab import get_remote_connection
from senaite.referral.selective import get_partition_laboratory
from senaite.referral.selective import is_referral_partition
from senaite.referral.utils import get_field_value
from senaite.referral.utils import set_field_value
from senaite.referral.workflow import change_workflow_state
from senaite.referral.workflow import restore_referred_sample
from senaite.referral.workflow import ship_sample
from zope.interface import alsoProvides


@check_installed(None)
def AfterTransitionEventHandler(sample, event): # noqa lowercase
    """Actions to be done just after a transition for a sample takes place
    """
    if not event.transition:
        return

    if event.transition.id == "no_sampling_workflow":
        after_no_sampling_workflow(sample)

    if event.transition.id == "ship":
        after_ship(sample)

    if event.transition.id == "verify":
        after_verify(sample)

    if event.transition.id == "reject":
        after_reject(sample)

    if event.transition.id == "invalidate":
        after_invalidate(sample)

    if event.transition.id == "invalidate_at_reference":
        after_invalidate_at_reference(sample)

    if event.transition.id == "reject_at_reference":
        after_reject_at_reference(sample)

    if event.transition.id == "recall_from_shipment":
        restore_referred_sample(sample)


def after_no_sampling_workflow(sample):
    """Automatically receive and ship samples for which an outbound shipment
    has been specified on creation (Add sample form)
    """
    shipment = sample.getOutboundShipment()
    if not shipment:
        return

    # Transition the sample to received + shipped status
    doActionFor(sample, "receive")
    ship_sample(sample, shipment)


def after_ship(sample):
    """Automatically transitions the analyses from the sample to referred status
    """
    for analysis in sample.getAnalyses(full_objects=True):
        doActionFor(analysis, "refer")


def after_verify(sample):
    """Notify the referring laboratory about this sample
    """
    shipment = sample.getInboundShipment()
    if not shipment:
        return

    # Notify the referring laboratory
    lab = shipment.getReferringLaboratory()
    remote_lab = get_remote_connection(lab)
    if not remote_lab:
        return

    # Keep the sample catalog in sync even if the remote notification fails
    # and the caller handles the exception without aborting the transaction.
    sample.reindexObject()

    # Update the results for this sample in the remote lab
    remote_lab.update_analyses(sample)


def after_reject(sample):
    """Notify the referring laboratory about this sample
    """
    shipment = sample.getInboundShipment()
    if not shipment:
        return

    # Notify the referring laboratory
    lab = shipment.getReferringLaboratory()
    remote_lab = get_remote_connection(lab)
    if not remote_lab:
        return

    # Notify the sample was rejected to the referring lab
    remote_lab.do_action(sample, "reject")


def get_remote_lab(shipment):
    """Returns the remote laboratory assigned to the given shipment, if any
    """
    lab = None
    if IInboundSampleShipment.providedBy(shipment):
        lab = shipment.getReferringLaboratory()
    elif IOutboundSampleShipment.providedBy(shipment):
        lab = shipment.getReferenceLaboratory()
    return get_remote_connection(lab)


def after_invalidate(sample):
    """"Actions to do when invalidating a sample
    """
    shipment = sample.getInboundShipment()
    referring = get_remote_lab(shipment)
    if referring:
        # notify the referring laboratory
        referring.do_action(sample, "invalidate_at_reference")


def after_invalidate_at_reference(sample):
    """Actions to do when a sample is invalidated at reference laboratory
    """
    retest = sample.getRetest()
    if not retest:
        # transition this sample to 'invalid' status
        doActionFor(sample, "invalidate")
        retest = sample.getRetest()
    else:
        # change workflow manually, but omit action so no after events
        change_workflow_state(sample, SAMPLE_WORKFLOW, "invalid")

    # manually transition the retest to 'referred' status
    change_workflow_state(retest, SAMPLE_WORKFLOW, "shipped", action="ship")

    # and its analyses as well
    for analysis in retest.objectValues("Analysis"):
        change_workflow_state(analysis, ANALYSIS_WORKFLOW,
                              state_id="referred",
                              action="refer")

    if is_referral_partition(sample):
        synchronize_selective_retest(sample, retest)


def after_reject_at_reference(sample):
    """Reject only the canonical analyses delegated by this aliquot."""
    if not is_referral_partition(sample):
        return

    for technical in sample.getAnalyses(full_objects=True):
        source_uid = get_field_value(
            technical, "ReferralSourceAnalysisUID", ""
        )
        canonical = api.get_object(source_uid, default=None)
        if not canonical:
            continue
        if isTransitionAllowed(canonical, "reject"):
            doActionFor(canonical, "reject")
        else:
            set_workflow_state(
                canonical,
                ANALYSIS_WORKFLOW,
                "rejected",
                action="reject",
            )
            alsoProvides(canonical, IRejected)


def synchronize_selective_retest(invalidated, retest):
    """Correlate a remote sample retest with canonical analysis retests."""
    laboratory = get_partition_laboratory(invalidated)
    set_field_value(retest, "ReferralPartition", True)
    set_field_value(
        retest, "ReferralLaboratory", api.get_uid(laboratory)
    )
    retest.setOutboundShipment(None)
    if hasattr(retest, "setInternalUse"):
        retest.setInternalUse(True)
    if hasattr(retest, "setInvoiceExclude"):
        retest.setInvoiceExclude(True)

    new_by_keyword = {}
    for analysis in retest.getAnalyses(full_objects=True):
        new_by_keyword.setdefault(analysis.getKeyword(), []).append(analysis)

    for technical in invalidated.getAnalyses(full_objects=True):
        source_uid = get_field_value(
            technical, "ReferralSourceAnalysisUID", ""
        )
        canonical = api.get_object(source_uid, default=None)
        if not canonical:
            continue

        canonical_retest = canonical.getRetest()
        if not canonical_retest:
            canonical_retest = create_analysis_retest(canonical)
            set_workflow_state(
                canonical,
                ANALYSIS_WORKFLOW,
                "retracted",
                action="retract",
            )
            alsoProvides(canonical, IRetracted)

        candidates = new_by_keyword.get(technical.getKeyword(), [])
        if not candidates:
            continue
        new_technical = candidates.pop(0)
        delegation_id = get_field_value(
            technical, "ReferralDelegationID", ""
        )
        set_field_value(
            canonical_retest, "ReferralDelegationID", delegation_id
        )
        set_field_value(
            canonical_retest,
            "ReferralTechnicalAnalysisUID",
            api.get_uid(new_technical),
        )
        set_field_value(
            new_technical, "ReferralDelegationID", delegation_id
        )
        set_field_value(
            new_technical,
            "ReferralSourceAnalysisUID",
            api.get_uid(canonical_retest),
        )
        canonical_retest.reindexObject()

        # The old partition is invalid, so its technical analysis must not
        # remain apparently verified. The replacement already exists in the
        # partition retest, therefore change the state without triggering the
        # regular retract event (which would create another analysis retest).
        if api.get_review_status(technical) != "retracted":
            set_workflow_state(
                technical,
                ANALYSIS_WORKFLOW,
                "retracted",
                action="retract",
            )
            alsoProvides(technical, IRetracted)
        technical.reindexObject()
        new_technical.reindexObject()

    # The physical aliquot is already at the reference lab, so this technical
    # retest can receive results without creating another shipment.
    set_workflow_state(
        retest,
        SAMPLE_WORKFLOW,
        "received_at_reference",
        action="receive_at_reference",
    )
    retest.reindexObject()

    # The samples listing reads descendant UIDs from the primary sample's
    # catalog metadata. Refresh that metadata so the new partition retest is
    # displayed immediately when the primary sample row is expanded.
    parent = retest.getParentAnalysisRequest()
    if parent:
        parent.reindexObject(idxs=["getDescendantsUIDs"])
