from __future__ import print_function
import asyncio
import discord
import locale
import os
import pickle
import platform
import sqlite3
import threading
import time
import twitch
from datetime import datetime, timedelta
from discord.ext import commands
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# florians stream check funktionen
# If modifying these scopes, delete the file token.pickle.
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Database
conn = sqlite3.connect('NBombs.db', check_same_thread=False)


# loading environmental variables
load_dotenv()
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

# global variables
systemOS = platform.system()

# probably breaks if neither windows nor linux is used but at that point the whole bot is probably dead
if systemOS == "Windows":
    locale.setlocale(category=locale.LC_ALL,
                     locale="German")
else:
    locale.setlocale(category=locale.LC_ALL,
                     locale="de_DE.utf8")


def getSchedule():
    """Shows basic usage of the Google Calendar API.
    Prints the start and name of the next 10 events on the user's calendar.
    """
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    service = build('calendar', 'v3', credentials=creds)

    # Call the Calendar API
    now = datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
    # print('Getting the upcoming 10 events')
    events_result = service.events().list(calendarId='cjiotkbl350170iuoaqi6gqjao@group.calendar.google.com', timeMin=now,  # pylint: disable=maybe-no-member
                                          maxResults=10, singleEvents=True,
                                          orderBy='startTime').execute()
    events = events_result.get('items', [])

    # if not events:
    #     print('No upcoming events found.')
    # for event in events:
    #     start = event['start'].get('dateTime', event['start'].get('date'))
    #     print(start, event['summary'])
    return events

# checks if Rheyces stream is online 15 min after it was schedule to be online
# if its online takenPlace is set to 1 otherwise to 2


async def checkStreamLive():
    # new cursor and select all streams
    checkStreamCursor = conn.cursor()
    checkStreamCursor.execute("""
                        SELECT scheduledStartTime, scheduledEndTime, takenPlace
                        FROM floStreamSchedule
                        WHERE startedLate IS NULL
                        OR endedEarly IS NULL
                        """)
    rows = checkStreamCursor.fetchall()

    now = datetime.now()
    # iterate over all streams and look if any of them should be live now
    for stream in rows:
        # checking if we are less than 15 minutes away
        # then wait until we are 15min behind the supposed stream start and check if the stream is running
        if stream[0]:
            minutes_diff = (now - (datetime.strptime(
                stream[0], "%x - %H:%M:%S"))).total_seconds() / 60.0

            if minutes_diff < 15.0 and minutes_diff > 0.0:
                # wait until 15min after stream start
                await asyncio.sleep(int((15 - minutes_diff) * 60))
                # twitch api check if Rheyces live
                helix = twitch.Helix(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
                if helix.user("Rheyces").is_live:
                    # set the tags according to what we know so far
                    checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET takenPlace = 1, startedLate = 0
                                                WHERE scheduledStartTime = ?
                                                """, [stream[0]])
                    print("Stream ist rechtzeitig live.")
                else:
                    # set takenPlace to 2 if stream offline
                    checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET takenPlace = 2, startedLate = 0
                                                WHERE scheduledStartTime = ?
                                                """, [stream[0]])
                break
        # checking if we are less than 30 and more than 15 minutes away from the stream end
        # then wait until exactly 15 min to check if stream is still running
        if stream[1]:
            minutes_diff = ((datetime.strptime(
                stream[1], "%x - %H:%M:%S")) - now).total_seconds() / 60.0

            if minutes_diff < 30.0 and minutes_diff > 15.0:
                # wait until 15min before stream end
                await asyncio.sleep(int((minutes_diff-15) * 60))
                # twitch api check if Rheyces live
                helix = twitch.Helix(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
                if helix.user("Rheyces").is_live:
                    # set the tags according to the knowledge earned in the end
                    if stream[2] == 2:
                        checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET takenPlace = 1, startedLate = 1, endedEarly = 0
                                                WHERE scheduledEndTime = ?
                                                """, [stream[1]])
                        print("Stream ist zu spät gestartet.")
                    else:
                        checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET takenPlace = 1, startedLate = 0, endedEarly = 0
                                                WHERE scheduledEndTime = ?
                                                """, [stream[1]])
                        print("Stream ist rechtzeitig zuende.")
                else:
                    if stream[2] == 1:
                        checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET endedEarly = 1
                                                WHERE scheduledEndTime = ?
                                                """, [stream[1]])
                        print("Stream ist zu früh vorbei.")
                    else:
                        checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET endedEarly = 0
                                                WHERE scheduledEndTime = ?
                                                """, [stream[1]])
                        print("Stream ist ausgefallen.")
                break
    # close cursor and set boolean to false
    conn.commit()
    checkStreamCursor.close()

# checks the public google calendar for the next 10 upcoming events in this case Rheyces streams


async def checkSchedule():
    checkScheduleCursor = conn.cursor()
    startStreams = list()
    endStreams = list()
    events = getSchedule()

    # return if there are no scheduled events
    if not events:
        return
    # get every event start and end time
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        end = event['end'].get('dateTime', event['end'].get('date'))
        # convert both time formats
        scheduledStart = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S%z")
        scheduledStartLocal = datetime.strftime(
            scheduledStart, "%x - %H:%M:%S")
        startStreams.append(scheduledStartLocal)
        scheduledEnd = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S%z")
        scheduledEndLocal = datetime.strftime(
            scheduledEnd, "%x - %H:%M:%S")
        endStreams.append(scheduledEndLocal)

        # insert the stream if the exact same stream doesn't exist
        for (start, end) in zip(startStreams, endStreams):
            checkScheduleCursor.execute("""
                                INSERT INTO floStreamSchedule
                                (scheduledStartTime, scheduledEndTime, takenPlace)
                                    SELECT ?, ?, 0
                                    WHERE NOT EXISTS(
                                        SELECT *
                                        FROM floStreamSchedule
                                        WHERE scheduledStartTime = ?
                                        AND scheduledEndTime = ?
                                    )
                                """, (start, end, start, end))

    # commit
    conn.commit()

    checkScheduleCursor.close()
