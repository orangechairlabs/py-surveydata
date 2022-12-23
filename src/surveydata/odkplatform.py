#  Copyright (c) 2022 Orange Chair Labs LLC
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Support for ODK Central as a survey data platform."""

from surveydata import SurveyPlatform
from surveydata import StorageSystem
from pyodk.client import Client
import datetime
from dateutil import parser
import pandas as pd
from flatten_json import flatten


class ODKPlatform(SurveyPlatform):
    """ODK Central survey data platform implementation."""

    # define constants
    ID_FIELD = "KEY"                                    # unique submission ID field
    ID_FIELD_API = "__id"                               # unique submission ID field, as returned by API
    CURSOR_METADATA_ID = "__CURSOR__"                   # unique metadata ID for cursor (must start and end with __)
    REPEAT_GROUP_COLUMN_SUFFIX = "@odata.navigationLink"# suffix for columns representing repeat groups

    def __init__(self, config_file: str = None, project_id: int = None, form_id: str = ""):
        """
        Initialize ODK for access to survey data.

        :param config_file: Full path to ODK Central config file (needed to call sync_data())
        :type config_file: str
        :param project_id: ODK project ID (if not supplied, will use default_project_id in config file)
        :type project_id: int
        :param form_id: ODK form ID
        :type form_id: str

        If you're not going to call sync_data(), you don't need to supply any of the parameters to this constructor.
        """

        self.form_id = form_id
        if config_file:
            # go ahead and initialize the ODK Central client
            self.client = Client(config_path=config_file, project_id=project_id)
            self.client.open()

            # initialize project ID
            self.project_id = self.client.project_id
        else:
            # allow for initialization without sync_data() support
            self.client = None
            self.project_id = None

        # call base class constructor as well
        super().__init__()

    def sync_data(self, storage: StorageSystem, attachment_storage: StorageSystem = None,
                  no_attachments: bool = False, include_rejected: bool = False) -> list:
        """
        Sync survey data to storage system.

        :param storage: Storage system for submissions (and attachments, if supported and other options don't override)
        :type storage: StorageSystem
        :param attachment_storage: Separate storage system for attachments (only if needed)
        :type attachment_storage: StorageSystem
        :param no_attachments: True to not sync attachments
        :type no_attachments: bool
        :param include_rejected: True to include rejected submissions
        :type include_rejected: bool
        :return: List of new submissions stored (submission ID strings)
        :rtype: list
        """

        # fire an exception if we haven't been initialized for syncing
        if self.client is None or not self.form_id:
            raise ValueError(
                "ODKPlatform not initialized with parameters sufficient for syncing data (config_file, form_id).")

        # decide where attachments should go (if anywhere)
        if no_attachments:
            attachment_storage = None
        elif attachment_storage is None:
            attachment_storage = storage

        # fetch current cursor from storage
        cursor = storage.get_metadata(self.CURSOR_METADATA_ID)

        # set our filter for pulling data
        if cursor:
            submission_filter = f"(__system/updatedAt ge {cursor} or __system/submissionDate ge {cursor})"
        else:
            submission_filter = ""
        if not include_rejected:
            if submission_filter:
                submission_filter += " and "
            submission_filter += "__system/reviewState ne 'rejected'"

        # pull data via ODK API, including expansion for repeat-group data
        response_data = self.client.submissions.get_table(form_id=self.form_id, expand="*", filter=submission_filter)

        new_submission_list = []
        if response_data:
            # extract data as flattened DataFrame
            df = pd.DataFrame([flatten(x, '/') for x in response_data['value']])

            # rename __id column to KEY for basic consistency with ODK Central export format
            df.rename(columns={self.ID_FIELD_API: self.ID_FIELD}, inplace=True)

            # scan for repeat groups, to tidy things up a little
            repeat_group_cols = [col for col in df if col.endswith(self.REPEAT_GROUP_COLUMN_SUFFIX)]
            if repeat_group_cols:
                # drop the OData navigation columns
                df.drop(columns=repeat_group_cols, inplace=True)
                for repeat_group_col in repeat_group_cols:
                    repeat_group = repeat_group_col[:-len(self.REPEAT_GROUP_COLUMN_SUFFIX)]
                    id_cols = [col for col in df if col.startswith(f"{repeat_group}/") and col.endswith("/__id")]
                    if id_cols:
                        # drop the individual repeat-group row ID columns
                        df.drop(columns=id_cols, inplace=True)

            # start out presuming that the last submission is the one last touched
            newcursor = df["__system/updatedAt"].iloc[-1]
            if not newcursor:
                newcursor = df["__system/submissionDate"].iloc[-1]
            newcursor_dt = parser.parse(newcursor)

            # loop through to process each submission
            for index, submission in df.iterrows():
                # find when the submission was last touched
                if submission["__system/updatedAt"]:
                    last_touched = submission["__system/updatedAt"]
                else:
                    last_touched = submission["__system/submissionDate"]
                last_touched_dt = parser.parse(last_touched)

                # if the submission's last_touched is greater than our presumed new cursor, use it instead
                if last_touched_dt > newcursor_dt:
                    newcursor = last_touched
                    newcursor_dt = last_touched_dt

                # generally, we want to write submissions to storage, even if we already have them — but, for
                # efficiency reasons, we don't want to keep re-storing the most recent submission when it matches
                # the cursor we used for the query (since the API query is inclusive of the date used in the cursor)
                subid = submission[self.ID_FIELD]
                if last_touched != cursor or not storage.query_submission(subid):
                    # if we have somewhere to save attachments — and it supports attachments — save them first, if any
                    if attachment_storage is not None and attachment_storage.attachments_supported() \
                            and submission["__system/attachmentsPresent"] > 0:

                        response = self.client.get(
                            f"projects/{self.project_id}/forms/{self.form_id}/submissions/{subid}/attachments")

                        for attachment in response.json():
                            if attachment["exists"]:
                                attachment_name = attachment["name"]
                                # stream the file from the server
                                attresponse = self.client.get(f"projects/{self.project_id}/forms/{self.form_id}/"
                                                              f"submissions/{subid}/attachments/{attachment_name}",
                                                              stream=True)
                                # raise errors as exceptions
                                attresponse.raise_for_status()
                                # stream straight to storage
                                attresponse.raw.decode_content = True
                                attachment_storage.store_attachment(subid, attachment_name=attachment_name,
                                                                    attachment_data=attresponse.raw)

                    # finally, save the submission itself and remember in list of new submissions
                    storage.store_submission(subid, submission.to_dict())
                    new_submission_list += [subid]

            # update our cursor, if it changed
            if newcursor != cursor:
                storage.store_metadata(self.CURSOR_METADATA_ID, newcursor)

                # since the cursor changed, go ahead an set the appropriate (API) timezone as well
                storage.set_data_timezone(datetime.timezone.utc)

        return new_submission_list

    @staticmethod
    def get_submissions_df(storage: StorageSystem) -> pd.DataFrame:
        """
        Get all submission data from storage, organized into a Pandas DataFrame and optimized based on the platform.

        :param storage: Storage system for submissions
        :type storage: StorageSystem
        :return: Pandas DataFrame containing all submissions currently in storage
        :rtype: pandas.DataFrame
        """

        # convert to DataFrame
        submissions_df = storage.get_submissions_df()

        # set to index by KEY
        submissions_df.set_index([ODKPlatform.ID_FIELD], inplace=True)
        submissions_df = submissions_df.sort_index()

        return submissions_df
