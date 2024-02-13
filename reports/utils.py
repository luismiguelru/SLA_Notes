# -*- coding: utf-8 -*-
#
# Copyright (c) 2023, CloudBlue
# All rights reserved.
#

from datetime import datetime
from threading import Lock
import collections
import requests
import Levenshtein as levenshtein
import re

def convert_to_datetime(param_value):
    if param_value == "" or param_value == "-":
        return "-"

    return datetime.strptime(
        param_value.replace("T", " ").replace("+00:00", ""),
        "%Y-%m-%d %H:%M:%S",
    )


def today_str():
    return datetime.today().strftime('%Y-%m-%d %H:%M:%S')


def get_basic_value(base, value):
    if base and value in base:
        return base[value]
    return '-'


def get_value(base, prop, value):
    if prop in base:
        return get_basic_value(base[prop], value)
    return '-'


class Progress:
    def __init__(self, callback, total):
        self.lock = Lock()
        self.current = 0
        self.total = total
        self.callback = callback

    def increment(self):
        self.lock.acquire()
        self.current += 1
        self.callback(self.current, self.total)
        self.lock.release()


def get_dict_element(dictionary, *keys):
    if not keys or keys[0] not in dictionary:
        if not dictionary or isinstance(dictionary, collections.abc.Mapping):
            return ''
        return dictionary
    key = keys[0]
    return get_dict_element(dictionary[key], *keys[1:])

def convert_list_jira_info(data,jira_api_token):
    result_list = []
    for notes, ids in data.items():
        new_data = {'ID': ', '.join(ids), 'Notes': notes}
        result_list.append(update_jira_info(new_data,jira_api_token))
    return result_list
def update_jira_info(data,jira_api_token):

    all_ids = data['ID'].split(', ')
    jira_tickets = {}
    jira_statuses = {}
    ids_not_in_jira = []

    for id_str in all_ids:
        id_str = id_str.strip()
        id_match = re.match(r'PR-\d{4}-\d{4}-\d{4}-\d{3}', id_str)

        if not id_match:
            print(f"Invalid ID format: {id_str}")
            continue
        jira_ticket, jira_status = search_in_jira(id_str,jira_api_token)


        if jira_ticket == 'No ticket':
            ids_not_in_jira.append(id_str)
            jira_tickets[id_str] = None
            jira_statuses[id_str] = None
        else:
            jira_tickets[id_str] = jira_ticket
            jira_statuses[id_str] = jira_status


    if ids_not_in_jira:
            # Create a Jira issue for IDs that do not exist in Jira
        created_tickets = create_jira_issue(data['Notes'], ids_not_in_jira,jira_api_token)

        if created_tickets:
            # Update jira_tickets and jira_statuses based on created_tickets information
            for ticket_key in created_tickets:
                for id_str in ids_not_in_jira:
                    jira_tickets[id_str] = ticket_key
                    jira_statuses[id_str] = 'Open'

        # If all IDs exist in Jira, print the ticket number; otherwise, print 'No ticket'
    data['JIRA TICKET'] = [jira_tickets[id_str] for id_str in all_ids] if all(
        ticket != 'No ticket' for ticket in jira_tickets.values()) else ['No ticket']
    data['JIRA STATUS'] = [jira_statuses[id_str] for id_str in all_ids] if all(
        status is not None for status in jira_statuses.values()) else ['N/A']

    return data

def search_in_jira(pr_id,jira_api_token):
    # Replace this with your actual JIRA API endpoint and authentication method
    jira_url = "https://jira.int.zone/rest/api/2/search"
    headers = {
        "Authorization": f"Bearer {jira_api_token}",
        "Content-Type": "application/json"
    }
    search_word = pr_id

    query = {
        'jql': f'text ~ "{search_word}"'
    }

    try:
        response = requests.post(jira_url, headers=headers, json=query)
        response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
        issues = response.json()

        if 'issues' in issues and issues['issues']:
            # get details from the first issue if there are multiple matches
            issue = issues['issues'][0]
            jira_ticket = issue['key']
            jira_status = issue['fields'].get('status', {}).get('name', '')
            return jira_ticket, jira_status
        else:
            return 'No ticket', 'N/A'
    except requests.exceptions.RequestException as e:
        print("Error occurred while searching issues:", e)
        return 'No ticket', 'N/A'

def create_jira_issue(notes, ids_not_in_jira,jira_api_token):
    jira_url = "https://jira.int.zone/rest/api/2/issue"

    headers = {
        "Authorization": f"Bearer {jira_api_token}",
        "Content-Type": "application/json"
    }

    data = {
        "fields": {
            "summary": "Ticket created by SLA Report Automation",
            "issuetype": {
                "name": "3rd-line Ticket"
            },
            "duedate": "2024-10-28",
            "project": {
                "key": "TRITS"
            },
            "description": f"This is being created automatically by SLA Report Automation. The reason: {notes}\nID(s): {', '.join(ids_not_in_jira)}",
        }
    }

    response = requests.post(jira_url, headers=headers, json=data)

    if response.status_code == 201:
        print(f"Issue created successfully for IDs: {', '.join(ids_not_in_jira)}")
        print("Issue Key:", response.json()["key"])
        return [response.json()["key"]]
    else:
        print(f"Failed to create issue for IDs: {', '.join(ids_not_in_jira)}. Status code: {response.status_code}")
        print("Response content:", response.text)
        return [response.json()["key"]]


def calculate_similarity(str1, str2):
    len_max = max(len(str1), len(str2))
    if len_max == 0:
        return 0.0
    distance = levenshtein.distance(str1, str2)
    similarity = 1.0 - distance / len_max
    return similarity

def check_report_generation(report,client):
    for item in report:
        id_list = item['ID'].split(', ')
        jira_tickets_str = item.get('JIRA TICKET', "")

        # Ensure jira_tickets_str is a string
        if isinstance(jira_tickets_str, list):
            jira_tickets_str = ', '.join(jira_tickets_str)
        jira_tickets = [ticket.strip() for ticket in jira_tickets_str.split(',')] if jira_tickets_str else []

        for id_str in id_list:
            jira_ticket_found = False

            for jira_ticket in jira_tickets:
                 # Replace conversation_id with the actual conversation ID from your data
                messages = client.conversations[id_str].messages.filter(type='message').all().values_list(
                    'id', 'type', 'text',
                )

                for message_value in messages:
                    text_message = message_value['text']

                    if jira_ticket in text_message:
                        jira_ticket_found = True
                        break

                if jira_ticket_found:
                    break

            if not jira_ticket_found:
                message_data = {"text": f"We have created a ticket {jira_ticket}"}
                client.conversations[id_str].messages.create(json=message_data)
    return report