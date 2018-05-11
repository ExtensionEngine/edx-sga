"""
This block defines a Staff Graded Assignment.  Students are shown a rubric
and invited to upload a file which is then graded by staff.
"""
import datetime
import hashlib
import json
import logging
import mimetypes
import os
import pkg_resources
import pytz
import StringIO
import zipfile

from courseware.models import StudentModule
from django.db.models import Q
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.files import File
from django.core.files.storage import default_storage
from django.template import Context, Template
from functools import partial
from student.models import CourseEnrollment, anonymous_id_for_user, user_by_anonymous_id
from submissions import api as submissions_api
from submissions.models import StudentItem as SubmissionsStudent
from webob.response import Response
from xblock.core import XBlock
from xblock.exceptions import JsonHandlerError
from xblock.fields import Boolean, DateTime, Float, Integer, Scope, String
from xblock.fragment import Fragment
from xmodule.util.duedate import get_extended_due_date

log = logging.getLogger(__name__)
BLOCK_SIZE = 2**10 * 8  # 8kb
DATETIME_FORMAT = '%m/%d/%Y %-I:%M%p'


def reify(meth):
    """
    Property which caches value so it is only computed once.
    """
    def getter(inst):
        value = meth(inst)
        inst.__dict__[meth.__name__] = value
        return value
    return property(getter)


class StaffGradedAssignmentXBlock(XBlock):
    """
    This block defines a Staff Graded Assignment.  Students are shown a rubric
    and invited to upload a file which is then graded by staff.
    """
    has_score = True
    icon_class = 'problem'
    show_in_read_only_mode = True

    display_name = String(
        default='Staff Graded Assignment', scope=Scope.settings,
        help="This name appears in the horizontal navigation at the top of "
             "the page."
    )

    weight = Float(
        display_name="Problem Weight",
        help=("Defines the number of points each problem is worth. "
              "If the value is not set, the problem is worth the sum of the "
              "option point values."),
        values={"min": 0, "step": 1},
        default=100,
        scope=Scope.settings
    )

    comment = String(
        display_name="Instructor comment",
        default='',
        scope=Scope.user_state,
        help="Feedback given to student by instructor."
    )

    annotated_sha1 = String(
        display_name="Annotated SHA1",
        scope=Scope.user_state,
        default=None,
        help=("sha1 of the annotated file uploaded by the instructor for "
              "this assignment.")
    )

    annotated_filename = String(
        display_name="Annotated file name",
        scope=Scope.user_state,
        default=None,
        help="The name of the annotated file uploaded for this assignment."
    )

    annotated_mimetype = String(
        display_name="Mime type of annotated file",
        scope=Scope.user_state,
        default=None,
        help="The mimetype of the annotated file uploaded for this assignment."
    )

    annotated_timestamp = DateTime(
        display_name="Timestamp",
        scope=Scope.user_state,
        default=None,
        help="When the annotated file was uploaded"
    )

    grades_published = Boolean(
        display_name='Display grade to students',
        scope=Scope.user_state_summary,
        default=False,
        help='Indicates if the grades will be displayed to students.'
    )

    def max_score(self):
        return self.weight or 100

    @reify
    def block_id(self):
        # cargo culted gibberish
        return self.scope_ids.usage_id

    def student_submission_id(self, id=None):
        """
        Returns dict required by the submissions app for creating and
        retrieving submissions for a particular student.
        """
        if id is None:
            id = self.xmodule_runtime.anonymous_student_id
            assert id != 'MOCK', "Forgot to call 'personalize' in test."
        return {
            "student_id": id,
            "course_id": self.course_id,
            "item_id": self.block_id,
            "item_type": 'sga',  # ???
        }

    def get_submission(self, id=None):
        """
        Get student's most recent submission.
        """
        submissions = submissions_api.get_submissions(
            self.student_submission_id(id))
        if submissions:
            # If I understand docs correctly, most recent submission should
            # be first
            return submissions[0]

    def get_score(self, id=None):
        """
        Get student's current score.
        """
        score = submissions_api.get_score(self.student_submission_id(id))
        if score:
            return score['points_earned']

    @reify
    def score(self):
        return self.get_score()

    def student_view(self, context=None):
        """
        The primary view of the StaffGradedAssignmentXBlock, shown to students
        when viewing courses.
        """
        context = {
            "student_state": json.dumps(self.student_state()),
            "id": self.location.name.replace('.', '_'),
            "grades_published": self.grades_published,
            "max_score": self.max_score(),
        }
        if self.show_staff_grading_interface():
            context['is_course_staff'] = True
            self.update_staff_debug_context(context)

        fragment = Fragment()
        fragment.add_content(
            render_template(
                'templates/staff_graded_assignment/show.html',
                context
            )
        )
        fragment.add_css(_resource("static/css/edx_sga.css"))
        fragment.add_javascript(_resource("static/js/src/edx_sga.js"))
        fragment.initialize_js('StaffGradedAssignmentXBlock', {'gradesPublished': self.grades_published})
        return fragment

    def update_staff_debug_context(self, context):
        published = self.start
        context['is_released'] = published and published < _now()
        context['location'] = self.location
        context['category'] = type(self).__name__
        context['fields'] = [
            (name, field.read_from(self))
            for name, field in self.fields.items()]

    def student_state(self):
        """
        Returns a JSON serializable representation of student's state for
        rendering in client view.
        """
        submission = self.get_submission()
        if submission:
            uploaded = {"filename": submission['answer']['filename']}
        else:
            uploaded = None

        if self.annotated_sha1:
            annotated = {"filename": self.annotated_filename}
        else:
            annotated = None

        score = self.score
        if score is not None:
            graded = {'score': score, 'comment': self.comment}
        else:
            graded = None

        return {
            "uploaded": uploaded,
            "annotated": annotated,
            "graded": graded,
            "max_score": self.max_score(),
            "upload_allowed": self.upload_allowed(),
        }

    def staff_grading_data(self):
        def get_student_data():
            # Submissions doesn't have API for this, just use model directly
            students = SubmissionsStudent.objects.filter(
                course_id=self.course_id,
                item_id=self.block_id)
            for student in students:
                submission = self.get_submission(student.student_id)
                user = user_by_anonymous_id(student.student_id)
                if not submission or user.is_staff or user.is_superuser:
                    continue
                module, _ = StudentModule.objects.get_or_create(
                    course_id=self.course_id,
                    module_state_key=self.location,
                    student=user,
                    defaults={
                        'state': '{}',
                        'module_type': self.category,
                    })
                state = json.loads(module.state)
                score = self.get_score(student.student_id)
                downloaded = self.get_submission_download_status(student.student_id)
                yield {
                    'module_id': module.id,
                    'student_id': student.student_id,
                    'submission_id': submission['uuid'],
                    'username': module.student.username,
                    'fullname': module.student.profile.name,
                    'filename': submission['answer']["filename"],
                    'downloaded': downloaded,
                    'timestamp': str(submission['created_at']),
                    'score': score,
                    'annotated': state.get("annotated_filename"),
                    'comment': state.get("comment", ''),
                }

        enrolled_students = CourseEnrollment.objects.users_enrolled_in(self.course_id).exclude(
            Q(is_staff=True) | Q(is_superuser=True)
        )
        submitted_student_data = list(get_student_data())
        submitted_student_ids = list(map((lambda x: user_by_anonymous_id(x['student_id']).id), submitted_student_data))
        not_submitted_students = enrolled_students.exclude(id__in=submitted_student_ids)

        assignments = submitted_student_data
        for student in not_submitted_students:
            assignments.append({
                'module_id': None,
                'student_id': student.id,
                'submission_id': None,
                'username': student.username,
                'fullname': student.profile.name,
                'filename': None,
                'downloaded': False,
                'timestamp': None,
                'score': None,
                'annotated': None,
                'comment': None,
            })

        return {
            'assignments': assignments,
            'max_score': self.max_score(),
            'passed_due': self.past_due(),
        }

    def studio_view(self, context=None):
        try:
            cls = type(self)

            def none_to_empty(x):
                return x if x is not None else ''
            edit_fields = (
                (field, none_to_empty(getattr(self, field.name)), validator)
                for field, validator in (
                    (cls.display_name, 'string'),
                    (cls.weight, 'number'))
            )

            context = {
                'fields': edit_fields
            }
            fragment = Fragment()
            fragment.add_content(
                render_template(
                    'templates/staff_graded_assignment/edit.html',
                    context
                )
            )
            fragment.add_javascript(_resource("static/js/src/studio.js"))
            fragment.initialize_js('StaffGradedAssignmentXBlockStudio')
            return fragment
        except:  # pragma: NO COVER
            log.error("Don't swallow my exceptions", exc_info=True)
            raise

    @XBlock.json_handler
    def save_sga(self, data, suffix=''):
        self.display_name = data.get('display_name', self.display_name)
        weight = data.get('weight')

        # Check that weight is a float.
        if weight:
            try:
                weight = float(weight)
            except ValueError:
                raise JsonHandlerError(400, 'Weight must be a decimal number')
            # Check that we are positive
            if weight <= 0:
                raise JsonHandlerError(
                    400, 'Weight must be a positive decimal number'
                )
        else:
            raise JsonHandlerError(400, 'Weight is a required field')
        self.weight = weight

    @XBlock.handler
    def update_grades_published(self, request, suffix=''):
        self.grades_published = json.loads(request.params.get('grades_published'))
        return Response(status=200)

    @XBlock.handler
    def upload_assignment(self, request, suffix=''):
        require(self.upload_allowed())
        upload = request.params['assignment']
        sha1 = _get_sha1(upload.file)
        answer = {
            "sha1": sha1,
            "filename": upload.file.name,
            "mimetype": mimetypes.guess_type(upload.file.name)[0],
        }
        student_id = self.student_submission_id()
        submissions_api.create_submission(student_id, answer)
        path = self._file_storage_path(sha1, upload.file.name)
        if not default_storage.exists(path):
            default_storage.save(path, File(upload.file))
        return Response(json_body=self.student_state())

    @XBlock.handler
    def staff_upload_annotated(self, request, suffix=''):
        require(self.is_course_staff())
        upload = request.params['annotated']
        module = StudentModule.objects.get(pk=request.params['module_id'])
        state = json.loads(module.state)
        state['annotated_sha1'] = sha1 = _get_sha1(upload.file)
        state['annotated_filename'] = filename = upload.file.name
        state['annotated_mimetype'] = mimetypes.guess_type(upload.file.name)[0]
        state['annotated_timestamp'] = _now().strftime(
            DateTime.DATETIME_FORMAT
        )
        path = self._file_storage_path(sha1, filename)
        if not default_storage.exists(path):
            default_storage.save(path, File(upload.file))
        module.state = json.dumps(state)
        module.save()
        return Response(json_body=self.staff_grading_data())

    @XBlock.handler
    def download_assignment(self, request, suffix=''):
        answer = self.get_submission()['answer']
        path = self._file_storage_path(answer['sha1'], answer['filename'])
        return self.download(path, answer['mimetype'], answer['filename'])

    @XBlock.handler
    def download_annotated(self, request, suffix=''):
        path = self._file_storage_path(
            self.annotated_sha1,
            self.annotated_filename,
        )
        return self.download(
            path,
            self.annotated_mimetype,
            self.annotated_filename
        )

    @XBlock.handler
    def staff_download(self, request, suffix=''):
        require(self.is_course_staff())
        submission = self.get_submission(request.params['student_id'])
        answer = submission['answer']
        path = self._file_storage_path(answer['sha1'], answer['filename'])
        self.set_submission_status_to_downloaded(request.params['student_id'])
        return self.download(path, answer['mimetype'], answer['filename'])

    @XBlock.handler
    def staff_download_annotated(self, request, suffix=''):
        require(self.is_course_staff())
        module = StudentModule.objects.get(pk=request.params['module_id'])
        state = json.loads(module.state)
        path = self._file_storage_path(
            state['annotated_sha1'],
            state['annotated_filename']
        )
        return self.download(
            path,
            state['annotated_mimetype'],
            state['annotated_filename']
        )

    def download(self, path, mimetype, filename):
        student_file = default_storage.open(path)
        app_iter = iter(partial(student_file.read, BLOCK_SIZE), '')
        return Response(
            app_iter=app_iter,
            content_type=mimetype,
            content_disposition="attachment; filename=" + filename)

    @XBlock.handler
    def download_submissions(self, request, suffix=''):
        require(self.is_course_staff())
        student_ids = json.loads(request.body).get('student_ids', [])
        files = self.get_files(self.get_submissions(student_ids))
        return self.download_zip(files)

    @XBlock.handler
    def download_all_submissions(self, request, suffix=''):
        require(self.is_course_staff())
        files = self.get_files(self.get_submissions())
        return self.download_zip(files)

    def get_submissions(self, student_ids=None):
        all_students = SubmissionsStudent.objects.filter(course_id=self.course_id, item_id=self.block_id)
        students = all_students.filter(student_id__in=student_ids) if student_ids else all_students
        submissions = []
        for student in students:
            submission_data = self.get_submission(student.student_id)
            user = user_by_anonymous_id(student.student_id)
            if submission_data and not (user.is_staff or user.is_superuser):
                self.set_submission_status_to_downloaded(student.student_id)
                submissions.append({
                    'username': user.username,
                    'data': submission_data
                })
        return submissions

    def get_files(self, submissions):
        files = []
        for submission in submissions:
            answer = submission['data']['answer']
            if answer['filename']:
                path = self._file_storage_path(answer['sha1'], answer['filename'])
                files.append({
                    'path': default_storage.path(path),
                    'name': '{}-{}'.format(submission['username'], answer['filename'])
                })
        return files

    def download_zip(self, student_files):
        zip_subdir = 'student_submissions'
        zip_filename = '{}.zip'.format(zip_subdir)

        # create StringIO object to serve as in-memory zip file
        sio = StringIO.StringIO()

        # add files to zip
        with zipfile.ZipFile(sio, 'w') as zf:
            for student_file in student_files:
                zip_path = os.path.join(zip_subdir, student_file['name'])
                zf.write(student_file['path'], zip_path)

        # save zip file and return its URL as JSON response
        return Response(json={'zip_url': default_storage.url(default_storage.save(zip_filename, sio))})

    def get_submission_download_status(self, student_id):
        return submissions_api.get_download_status(self.student_submission_id(student_id), self.user)

    def set_submission_status_to_downloaded(self, student_id):
        submissions_api.set_as_downloaded(self.student_submission_id(student_id), self.user)

    @XBlock.handler
    def get_staff_grading_data(self, request, suffix=''):
        require(self.is_course_staff())
        return Response(json_body=self.staff_grading_data())

    def validate_score_message(self, course_id, username):
        log.error(
            "enter_grade: invalid grade submitted for course:%s module:%s student:%s",
            course_id,
            self.location,
            username
        )
        return {
            "error": "Please enter valid grade"
        }

    def validate_score_over_max_message(self, course_id, username):
        log.error(
            "enter_grade: invalid grade (over max grade)submitted for course:%s module:%s student:%s",
            course_id,
            self.location,
            username
        )
        return {
            "error": "Please enter grade lower then {}".format(self.max_score())
        }

    def create_empty_submission(self, student_id):
        answer = {
            "sha1": None,
            "filename": None,
            "mimetype": None,
        }
        student_item_dict = self.student_submission_id(student_id)
        return submissions_api.create_submission(student_item_dict, answer)

    def create_empty_student_module(self, student):
        return StudentModule.objects.create(
            course_id=self.course_id,
            module_state_key=self.location,
            student=student,
            state=json.dumps({
                'comment': '',
                'annotated_sha1': None,
                'annotated_filename': None,
                'annotated_mimetype': None,
                'annotated_timestamp': None,
            }),
            module_type=self.category
        )

    @XBlock.handler
    def enter_grade(self, request, suffix=''):
        require(self.is_course_staff())
        score = request.params.get('grade')
        uuid = request.params.get('submission_id')
        module_id = request.params.get('module_id')
        student_id = request.params.get('student_id')

        if module_id:
            module = StudentModule.objects.get(pk=module_id)
        elif self.past_due():  # We allow grading student who haven't made a submission past due date.
            try:
                student_id = int(student_id)
                student = User.objects.get(id=student_id)
                student_id = anonymous_id_for_user(student, self.course_id)
            except ValueError:
                student = user_by_anonymous_id(student_id)
            module = self.create_empty_student_module(student)
            uuid = self.create_empty_submission(student_id)['uuid']
        else:
            msg = 'Module ID not provided.'
            log.error('SGA submission failed for {student_id}: {msg}'.format(
                student_id=student_id,
                msg=msg
            ))
            return Response(status=400, json_body={'error': msg})

        if not score:
            return Response(
                json_body=self.validate_score_message(
                    module.course_id,
                    module.student.username
                )
            )

        state = json.loads(module.state)
        try:
            score = float(score)
        except ValueError:
            return Response(
                json_body=self.validate_score_message(
                    module.course_id,
                    module.student.username
                )
            )

        if score > self.max_score():
            return Response(
                json_body=self.validate_score_over_max_message(
                    module.course_id,
                    module.student.username
                )
            )
        submissions_api.set_score(uuid, score, self.max_score())
        state['comment'] = request.params.get('comment', '')
        module.state = json.dumps(state)
        module.save()

        return Response(json_body=self.staff_grading_data())

    @XBlock.handler
    def remove_grade(self, request, suffix=''):
        require(self.is_course_staff())
        student_id = request.params['student_id']
        submissions_api.reset_score(student_id, unicode(self.course_id), unicode(self.block_id))
        module = StudentModule.objects.get(pk=request.params['module_id'])
        state = json.loads(module.state)
        state['comment'] = ''
        state['annotated_sha1'] = None
        state['annotated_filename'] = None
        state['annotated_mimetype'] = None
        state['annotated_timestamp'] = None
        module.state = json.dumps(state)
        module.save()
        return Response(json_body=self.staff_grading_data())

    @property
    def user(self):
        return User.objects.get(id=self.xmodule_runtime.user_id)

    def is_course_staff(self):
        return (
            getattr(self.xmodule_runtime, 'user_is_staff', False) or
            self.xmodule_runtime.get_user_role() == 'instructor'
        )

    def show_staff_grading_interface(self):
        in_studio_preview = self.scope_ids.user_id is None
        return self.is_course_staff() and not in_studio_preview

    def past_due(self):
        due = get_extended_due_date(self)
        if due is not None:
            return _now() > due
        return False

    def upload_allowed(self):
        return not self.past_due() and self.score is None

    def _file_storage_path(self, sha1, filename):
        path = (
            '{loc.org}/{loc.course}/{loc.block_type}/{loc.block_id}'
            '/{sha1}{ext}'.format(
                loc=self.location,
                sha1=sha1,
                ext=os.path.splitext(filename)[1]
            )
        )
        return path


def _get_sha1(file):
    sha1 = hashlib.sha1()
    for block in iter(partial(file.read, BLOCK_SIZE), ''):
        sha1.update(block)
    file.seek(0)
    return sha1.hexdigest()


def _resource(path):  # pragma: NO COVER
    """Handy helper for getting resources from our kit."""
    data = pkg_resources.resource_string(__name__, path)
    return data.decode("utf8")


def _now():
    return datetime.datetime.utcnow().replace(tzinfo=pytz.utc)


def load_resource(resource_path):  # pragma: NO COVER
    """
    Gets the content of a resource
    """
    resource_content = pkg_resources.resource_string(__name__, resource_path)
    return unicode(resource_content)


def render_template(template_path, context={}):  # pragma: NO COVER
    """
    Evaluate a template by resource path, applying the provided context
    """
    template_str = load_resource(template_path)
    template = Template(template_str)
    return template.render(Context(context))


def require(assertion):
    """
    Raises PermissionDenied if assertion is not true.
    """
    if not assertion:
        raise PermissionDenied
