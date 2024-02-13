# -*- coding: utf-8 -*-
#
# Copyright (c) 2024, Ingram Micro
# All rights reserved.
import datetime
import logging
import Levenshtein

from connect.client import R
from collections import defaultdict

from reports.utils import convert_to_datetime, get_dict_element,convert_list_jira_info,calculate_similarity,check_report_generation

def generate(
    client=None,
    parameters=None,
    progress_callback=None,
    renderer_type=None,
    extra_context_callback=None,
):
    # Initialize a dictionary to store IDs and their associated conversation notes
    id_conversation_notes_dict = {}
    try:
        import requests
        url = "https://jira.int.zone/rest/api/2/myself"
        headers = {
            "Authorization": f"Bearer {parameters['api_token']}"
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
    except requests.HTTPError as e:
        if response.status_code == 401:
            raise RuntimeError("Invalid token") from e
    except Exception as e:
        raise RuntimeError("Unexpected error occurred") from e

    try:
        offset_red = int(parameters['offset_red_days'])
        offset_yellow = int(parameters['offset_yellow_days'])

    except Exception:
        raise RuntimeError("Yellow and Red zone must be defined as amount of days")

    if offset_red <= offset_yellow:
        raise RuntimeError("Red zone must be for more days than yellow one")

    query = R()
    query &= R().status.eq('pending')
    if parameters.get('trans_type') and parameters['trans_type']['all'] is False:
        query &= R().asset.connection.type.oneof(parameters['trans_type']['choices'])
    if parameters.get('product') and parameters['product']['all'] is False:
        query &= R().asset.product.id.oneof(parameters['product']['choices'])
    requests = client.requests.filter(query).select(
        '-asset.items',
        '-asset.params',
        '-asset.configuration',
        '-activation_key',
        '-template',
    ).order_by('created')

    total = requests.count()

    progress = 0

    levels = {
        'red': offset_red,
        'yellow': offset_yellow,
    }

    jira_api_token=parameters["api_token"]


    for request in requests:
        yield _process_line(request, levels,client,jira_api_token)
        progress += 1
        progress_callback(progress, total)

def _get_awaiting_for(data):
    return (datetime.datetime.utcnow() - convert_to_datetime(data['created'])).days


def _get_contact(data):
    if not data:
        return ''
    last_name = get_dict_element(data, 'contact_info', 'contact', 'last_name')
    first_name = get_dict_element(data, 'contact_info', 'contact', 'first_name')
    return f'{last_name} {first_name}' if last_name and first_name else ''


def _get_sla_level(awaiting_days, levels):
    if awaiting_days >= levels['red']:
        return 'RED'
    elif awaiting_days >= levels['yellow']:
        return 'YELLOW'
    else:
        return 'GREEN'

def _get_latest_sla_indicator_message(client,data):
    conversation_id = data['id']
    messages = client.collection('conversations')[conversation_id].collection('messages').all()

    # Sort the messages based on the 'created' timestamp
    sorted_messages = sorted(messages, key=lambda x: x['created'], reverse=True)

    for message in sorted_messages:
        if "Indicator of Service Level Agreement" not in message['text']:
            return message["text"]
    return None

grouping_messages_cache = None

def _get_grouping_messages(client,jira_api_token):
    global grouping_messages_cache
    if grouping_messages_cache is None:
        grouping_messages_cache = _actual_get_grouping_messages(client,jira_api_token)
    return grouping_messages_cache

def _actual_get_grouping_messages(client,jira_api_token):
    pr_request_dict_list = []  # Initialize an empty list for dictionaries
    pr_request = client.requests.filter(status='pending').all()

    for row in pr_request:
        message = client.conversations[row['id']].messages.filter(type='message').all()

        for message_value in message:
            text_message = message_value['text']
            created_by_name = message_value['events']['created']['by'][
                'name'] if 'events' in message_value and 'created' in message_value['events'] else None

            if text_message is not None and created_by_name == "Luis Miguel Rodriguez Ugarte":
                pr_dict = {'ID': row['id'], 'Notes': text_message}
                pr_request_dict_list.append(pr_dict)
                break
    grouped_dict = defaultdict(list)
    first_common_part = None
    for pr_dict in pr_request_dict_list:
        found_group = False
        similarity_threshold = 0.9  # Adjust the threshold as needed

        for group_notes, group_ids in grouped_dict.items():
            similarity = calculate_similarity(pr_dict['Notes'], group_notes)
            if similarity >= similarity_threshold:
                found_group = True
                common_part_length = int(min(len(pr_dict['Notes']), len(group_notes)) * similarity)
                common_part = pr_dict['Notes'][:common_part_length]

                if first_common_part is None:
                    first_common_part = common_part
                break
            # If no similar group is found, create a new group
        if found_group and common_part:
            # Append to the existing group with common_part
            if group_notes != first_common_part:  # Check if key needs to be updated
                grouped_dict[first_common_part] = grouped_dict.pop(group_notes, [])  # Update key to common_part
            grouped_dict[first_common_part].append(pr_dict['ID'])  # Append ID to the group
        else:
            # Create a new group with the common_part
            grouped_dict[pr_dict['Notes']].append(pr_dict['ID'])

            # Use the generated report for Jira updates

    report_generation = convert_list_jira_info(grouped_dict, jira_api_token)
    check_report_generation(report_generation,client)

    return report_generation

def get_notes_for_id(report_generation, target_id):
    for item in report_generation:
        id_list = item['ID'].split(', ')
        for id_str in id_list:
            if id_str == target_id:
                return item.get('Notes', None)
    return None
def get_jira_ticket_and_status_for_id(report_generation, target_id):
    result = []
    for item in report_generation:
        id_list = item['ID'].split(', ')
        if target_id in id_list:
            index = id_list.index(target_id)
            jira_ticket = item.get('JIRA TICKET', [])[index] if len(item.get('JIRA TICKET', [])) > index else None
            jira_status = item.get('JIRA STATUS', [])[index] if len(item.get('JIRA STATUS', [])) > index else None
            result.append({
                'JIRA Ticket': jira_ticket,
                'JIRA Status': jira_status
            })
    return result

def get_jira_ticket_for_id(report_generation, target_id):
    jira_ticket_and_status = get_jira_ticket_and_status_for_id(report_generation, target_id)
    if jira_ticket_and_status:
        jira_ticket_list = [item['JIRA Ticket'] for item in jira_ticket_and_status]
        jira_ticket_str = ', '.join(jira_ticket_list)
        return jira_ticket_str
    return None

def get_jira_status_for_id(report_generation, target_id):
    jira_ticket_and_status = get_jira_ticket_and_status_for_id(report_generation, target_id)
    if jira_ticket_and_status:
        jira_status_list = [item['JIRA Status'] for item in jira_ticket_and_status]
        jira_status_str = ', '.join(jira_status_list)
        return jira_status_str
    return None
def _process_line(data, levels,client,jira_api_token):
    awaiting_for_days = _get_awaiting_for(data)
    sla_level = _get_sla_level(awaiting_for_days, levels)
    tier_sample = _get_grouping_messages(client,jira_api_token)

    return (
        data.get('id'),
        get_dict_element(data, 'asset', 'product', 'id'),
        get_dict_element(data, 'asset', 'product', 'name'),
        get_dict_element(data, 'asset', 'connection', 'vendor', 'id'),
        get_dict_element(data, 'asset', 'connection', 'vendor', 'name'),
        get_dict_element(data, 'asset', 'connection', 'provider', 'id'),
        get_dict_element(data, 'asset', 'connection', 'provider', 'name'),
        get_dict_element(data, 'type'),
        awaiting_for_days,
        convert_to_datetime(get_dict_element(data, 'created')),
        get_dict_element(data, 'status'),
        get_dict_element(data, 'asset', 'connection', 'type'),
        sla_level,
        get_notes_for_id(tier_sample,data.get('id')),
        get_jira_ticket_for_id(tier_sample,data.get('id')),
        get_jira_status_for_id(tier_sample,data.get('id')),
    )