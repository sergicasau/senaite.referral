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

import json

from bika.lims import api
from bika.lims.interfaces import IReceived
from bika.lims.utils.analysisrequest import create_analysisrequest
from bika.lims.utils.analysisrequest import receive_sample
from bika.lims.workflow import doActionFor
from senaite.core.catalog import SETUP_CATALOG
from senaite.referral.interfaces import IInboundSampleValues
from senaite.referral.selective import validate_local_service
from senaite.referral.utils import get_field_value
from senaite.referral.utils import set_field_value
from senaite.referral.workflow.analysisrequest import get_remote_lab
from zope.annotation.interfaces import IAnnotations
from zope.component import subscribers


def after_receive_inbound_sample(inbound_sample):
    """Event fired after a transition "receive_inbound_sample" for an
    InboundSample object is triggered. System creates the counterpart sample
    in current instance and tries to receive the whole inbound shipment
    afterwards
    """
    if inbound_sample.getRawSample():
        # There is a counterpart sample already
        return

    # Create the sample
    sample = create_sample(inbound_sample)

    # Always auto-receive samples from inbound shipments
    if not IReceived.providedBy(sample):
        receive_sample(sample)

    # Notify the referring laboratory about the sample's reception
    shipment = inbound_sample.getInboundShipment()
    referring = get_remote_lab(shipment)
    if referring:
        referring.do_action(sample, "receive_at_reference")

    # Try with the whole shipment
    doActionFor(shipment, "receive_inbound_shipment")


def after_reject_inbound_sample(inbound_sample):
    """Event fired after a transition "reject_inbound_sample" for an
    InboundSample object is triggered. If all inbound samples from the
    inbound shipment have been rejected and no regular samples have been
    created yet, the inbound shipment is automatically rejected as well
    """
    shipment = inbound_sample.getInboundShipment()
    for sample in shipment.getInboundSamples():
        if api.get_review_status(sample) != "rejected":
            return

    # All inbound samples have been rejected. Reject the shipment
    doActionFor(shipment, "reject_inbound_shipment")


def create_sample(inbound_sample):
    """Creates a counterpart sample for the inbound sample passed in
    """
    # Get the shipment that contains this inbound sample
    shipment = inbound_sample.getInboundShipment()

    # Get the default client for this shipment
    client = shipment.getReferringClient()
    client = api.get_object(client)

    # Get the default contact for this shipment
    contact = shipment.getRawDefaultContact()

    annotation = IAnnotations(inbound_sample)
    original = annotation.get("__original__", "{}")
    record = json.loads(original)
    selective = bool(record.get("selective_referral", False))

    # Create the sample object
    sample_type = inbound_sample.getSampleType()
    sample_type_uid = api.get_uid(get_sample_type_by_title(sample_type))

    # Resolve the service uids that match with the requested analyses
    keywords = inbound_sample.getAnalyses() or []
    services = [get_service_by_keyword(key) for key in keywords]
    if selective:
        list(map(validate_local_service, services))
    services_uids = list(map(api.get_uid, services))

    # Update the inbound sample with the service uids
    inbound_sample.setServices(services_uids)

    values = {
        "Client": api.get_uid(client),
        "Contact": contact,
        "ClientSampleID": inbound_sample.getReferringID(),
        "DateSampled": inbound_sample.getDateSampled(),
        "SampleType": sample_type_uid,
        "Priority": inbound_sample.getPriority(),
        "InboundShipment": api.get_uid(shipment),
        "ReferralOriginLabCode": record.get("origin_lab_code", ""),
        "ReferralPrimaryUID": record.get("primary_uid", ""),
        "ReferralPartitionUID": record.get("partition_uid", ""),
    }

    # Let optional add-ons provide site-specific mandatory fields (e.g. the
    # generic inter-laboratory Circuit in senaite.circuits).
    for adapter in subscribers((inbound_sample,), IInboundSampleValues):
        adapter.update(values, record)

    # Create the sample and assign the shipment
    request = api.get_request()
    sample = create_analysisrequest(client, request, values, services_uids)

    if selective:
        set_analysis_correlations(sample, record.get("analysis_records", []))

    # Associate this new Sample with the Inbound Sample
    inbound_sample.setSample(sample)
    return sample


def get_service_by_keyword(keyword):
    """Resolve one active Analysis Service by its globally unique Keyword."""
    query = {
        "portal_type": "AnalysisService",
        "getKeyword": keyword,
    }
    brains = api.search(query, SETUP_CATALOG)
    services = [api.get_object(brain) for brain in brains]
    services = list(filter(api.is_active, services))
    if not services:
        raise ValueError(
            "No active Analysis Service found for Keyword '{}'".format(
                keyword
            )
        )
    if len(services) > 1:
        raise ValueError(
            "More than one active Analysis Service uses Keyword '{}'".format(
                keyword
            )
        )
    return services[0]


def get_sample_type_by_title(title):
    """Resolve one active Sample Type by its stable title/code."""
    query = {
        "portal_type": "SampleType",
        "title": title,
    }
    brains = api.search(query, SETUP_CATALOG)
    sample_types = [api.get_object(brain) for brain in brains]
    sample_types = list(filter(api.is_active, sample_types))
    if not sample_types:
        raise ValueError("No active Sample Type found for '{}'".format(title))
    if len(sample_types) > 1:
        raise ValueError(
            "More than one active Sample Type uses title '{}'".format(title)
        )
    return sample_types[0]


def set_analysis_correlations(sample, records):
    """Copy origin-side correlation metadata to destination analyses."""
    records_by_keyword = {}
    for record in records:
        records_by_keyword.setdefault(record.get("keyword"), []).append(record)

    for analysis in sample.getAnalyses(full_objects=True):
        candidates = records_by_keyword.get(analysis.getKeyword(), [])
        if not candidates:
            raise ValueError(
                "Missing correlation record for delegated analysis '{}'"
                .format(analysis.getKeyword())
            )
        record = candidates.pop(0)
        delegation_id = record.get("delegation_id")
        source_uid = record.get("source_analysis_uid")
        if not delegation_id or not source_uid:
            raise ValueError(
                "Incomplete correlation record for delegated analysis '{}'"
                .format(analysis.getKeyword())
            )
        set_field_value(
            analysis, "ReferralDelegationID", delegation_id
        )
        set_field_value(
            analysis, "ReferralSourceAnalysisUID", source_uid
        )
        set_field_value(
            analysis,
            "ReferralOriginLabCode",
            get_field_value(sample, "ReferralOriginLabCode", ""),
        )
        analysis.reindexObject()
