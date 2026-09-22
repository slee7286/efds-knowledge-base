from email.message import Message
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import uuid

import pytest

from efds.db.models import Meeting, MeetingArtifact, SlackChannel, SlackChannelSyncSetting, SlackMessage
from efds.integrations.google_docs_meetings import (
    DocumentLink, discover_documents, fetch_document, save_document, sync_google_docs_meetings,
)


def response(body=b'Meeting notes', content_type='text/plain', url='https://docs.google.com/document/d/test/export'):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.headers = Message()
    result.headers['Content-Type'] = content_type
    result.geturl.return_value = url
    result.read.return_value = body
    return result


def test_export_normalizes_bom_and_line_endings():
    with patch('efds.integrations.google_docs_meetings.urlopen', return_value=response(b'\xef\xbb\xbfNotes\r\nText')):
        assert fetch_document('test') == 'Notes\nText'


@pytest.mark.parametrize('body,content_type,url', [
    (b'Sign in', 'text/html', 'https://accounts.google.com/signin'),
    (b'<html>Sign in</html>', 'text/plain', 'https://docs.google.com/export'),
    (b'', 'text/plain', 'https://docs.google.com/export'),
])
def test_rejects_login_html_and_empty_exports(body, content_type, url):
    with patch('efds.integrations.google_docs_meetings.urlopen', return_value=response(body, content_type, url)):
        with pytest.raises(ValueError):
            fetch_document('test')


def test_rejects_non_document_request_without_network():
    with patch('efds.integrations.google_docs_meetings.urlopen') as request:
        with pytest.raises(ValueError):
            fetch_document('../bad?redirect=elsewhere')
        request.assert_not_called()


def test_access_failure_is_clear_and_does_not_leak_response():
    with patch('efds.integrations.google_docs_meetings.urlopen', side_effect=HTTPError('private',403,'secret',{},None)):
        with pytest.raises(ValueError, match='HTTP 403') as error:
            fetch_document('test')
    assert 'secret' not in str(error.value)


def test_discovery_deduplicates_rich_links_and_requires_enabled_channel():
    session = Mock()
    session.get.side_effect = [SlackChannel(id='C1', name='meetings'), SlackChannelSyncSetting(enabled=True)]
    session.scalars.return_value.all.return_value = [SlackMessage(
        id=uuid.uuid4(), slack_ts='100.0', message_text='<https://docs.google.com/document/d/doc1/edit|notes>',
        raw_event={'attachments':[{'url':'https://docs.google.com/document/d/doc1/edit'},
                                  {'url':'https://docs.google.com/document/d/doc2/edit'}]})]
    docs = discover_documents(session, 'C1')
    assert [d.document_id for d in docs] == ['doc1','doc2']
    assert len(docs[0].references) == 1
    session.get.side_effect = [SlackChannel(id='C1', name='meetings'), SlackChannelSyncSetting(enabled=False)]
    with pytest.raises(ValueError, match='enabled'):
        discover_documents(session,'C1')


def test_reverted_notes_reuse_historical_version():
    session = Mock()
    meeting = Meeting(id=uuid.uuid4(),title='Notes',metadata_={})
    current = MeetingArtifact(content_hash='newer',is_current=True)
    old = MeetingArtifact(is_current=False)
    session.scalar.side_effect = [meeting,current,old]
    assert save_document(session,DocumentLink('doc1'),'Earlier notes',uuid.uuid4()) == 'updated'
    assert old.is_current and not current.is_current
    assert not any(isinstance(call.args[0],MeetingArtifact) for call in session.add.call_args_list)
    assert meeting.started_at is None


def test_dry_run_never_mutates_database_and_continues_after_inaccessible_doc():
    session = Mock()
    with patch('efds.integrations.google_docs_meetings.discover_documents',return_value=[DocumentLink('denied'),DocumentLink('ok')]):
        fetch = Mock(side_effect=[ValueError('HTTP 403'), 'Notes'])
        summary = sync_google_docs_meetings(session,'C1',dry_run=True,fetch=fetch)
    assert summary['fetched'] == 1
    assert len(summary['errors']) == 1
    session.add.assert_not_called()
    session.commit.assert_not_called()
