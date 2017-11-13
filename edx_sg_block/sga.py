"""
This block defines a Staff Graded Assignment.  Students are shown a rubric
and invited to upload a file which is then graded by staff.
"""
import datetime
import json
import logging
import pkg_resources
import pytz

from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.template import Context, Template

from courseware.models import StudentModule
from courseware.access import is_staff_or_instructor_on_course
from student.models import user_by_anonymous_id, CourseEnrollment
from submissions import api as submissions_api

from webob.response import Response

from xblock.core import XBlock
from xblock.exceptions import JsonHandlerError
from xblock.fields import DateTime, Scope, String, Float, Integer
from xblock.fragment import Fragment

from xmodule.util.duedate import get_extended_due_date


log = logging.getLogger(__name__)


def reify(meth):
    """
    Decorator which caches value so it is only computed once.
    Keyword arguments:
    inst
    """
    def getter(inst):
        """
        Set value to meth name in dict and returns value.
        """
        value = meth(inst)
        inst.__dict__[meth.__name__] = value
        return value
    return property(getter)


class StaffGradedXBlock(XBlock):
    """
    This block defines a Staff Graded Assignment.  Students are shown a rubric
    and invited to upload a file which is then graded by staff.
    """
    has_score = True
    icon_class = 'problem'
    show_in_read_only_mode = True

    display_name = String(
        default='Staff Graded Points', scope=Scope.settings,
        help="This name appears in the horizontal navigation at the top of "
             "the page."
    )

    points = Integer(
        display_name="Maximum score",
        help=("Maximum grade score given to assignment by staff."),
        default=100,
        scope=Scope.settings
    )

    weight = Float(
        display_name="Problem Weight",
        help=("Defines the number of points each problem is worth. "
              "If the value is not set, the problem is worth the sum of the "
              "option point values."),
        values={"min": 0, "step": .1},
        scope=Scope.settings
    )

    comment = String(
        display_name="Instructor comment",
        default='',
        scope=Scope.user_state,
        help="Feedback given to student by instructor."
    )

    def max_score(self):
        """
        Return the maximum score possible.
        """
        return self.points

    @reify
    def block_id(self):
        """
        Return the usage_id of the block.
        """
        return self.scope_ids.usage_id

    def student_submission_id(self, submission_id=None):
        # pylint: disable=no-member
        """
        Returns dict required by the submissions app for creating and
        retrieving submissions for a particular student.
        """
        if submission_id is None:
            submission_id = self.xmodule_runtime.anonymous_student_id
            assert submission_id != (
                'MOCK', "Forgot to call 'personalize' in test."
            )
        return {
            "student_id": submission_id,
            "course_id": self.course_id,
            "item_id": self.block_id,
            "item_type": 'sga',  # ???
        }

    def get_score(self, submission_id=None):
        """
        Return student's current score.
        """
        score = submissions_api.get_score(
            self.student_submission_id(submission_id)
        )
        if score:
            return score['points_earned']

    @reify
    def score(self):
        """
        Return score from submissions.
        """
        return self.get_score()

    def student_view(self, context=None):
        # pylint: disable=no-member
        """
        The primary view of the StaffGradedXBlock, shown to students
        when viewing courses.
        """
        context = {
            "student_state": json.dumps(self.student_state()),
            "id": self.location.name.replace('.', '_')
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
        fragment.add_javascript(_resource("static/js/src/jquery.tablesorter.min.js"))
        fragment.initialize_js('StaffGradedXBlock')
        return fragment

    def update_staff_debug_context(self, context):
        # pylint: disable=no-member
        """
        Add context info for the Staff Debug interface.
        """
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
        if self.xmodule_runtime.anonymous_student_id:
            user = user_by_anonymous_id(self.xmodule_runtime.anonymous_student_id)
        score = 0
        if user:
            student_record, created = StudentModule.objects.get_or_create(
                     course_id=self.course_id,
                     module_state_key=self.location,
                     student=user,
                     defaults={
                         'state': '{}',
                         'module_type': self.category,
                         'grade': 0,
                         'max_grade': self.max_score()
                     })
            score = student_record.grade
        if score is not None:
            graded = {'score': score, 'comment': self.comment}
        else:
            graded = None

        return {
            "display_name": self.display_name,
            "graded": graded,
            "max_score": self.max_score(),
        }

    def staff_grading_data(self):
        """
        Return student assignment information for display on the
        grading screen.
        """
        def get_student_data():
            # pylint: disable=no-member
            """
            Returns a dict of student assignment information along with student id and module id,
            this information will be used on grading screen
            """
            # Submissions doesn't have API for this, just use model directly.
            course_enrollments = CourseEnrollment.objects.filter(
                course_id=self.course_id
            ).exclude(
                Q(user__is_staff=True) | Q(user__is_superuser=True)
            )
            for course_enrollment in course_enrollments:
                student = course_enrollment.user
                if not is_staff_or_instructor_on_course(student, self.course_id):
                    module, created = StudentModule.objects.get_or_create(
                        course_id=self.course_id,
                        module_state_key=self.location,
                        student=student,
                        defaults={
                            'state': '{}',
                            'module_type': self.category,
                            'grade': 0,
                            'max_grade': self.max_score()
                        })
                    if created:
                        log.info(
                            "Init for course:%s module:%s student:%s  ",
                            module.course_id,
                            module.module_state_key,
                            module.student.username
                        )

                    state = json.loads(module.state)
                    yield {
                        'module_id': module.id,
                        'student_id': student.id,
                        'username': module.student.username,
                        'fullname': module.student.profile.name,
                        'score': module.grade,
                        'comment': state.get("comment", ''),
                    }

        return {
            'assignments': list(get_student_data()),
            'max_score': self.max_score(),
            'display_name': self.display_name
        }

    def studio_view(self, context=None):
        """
        Return fragment for editing block in studio.
        """
        try:
            cls = type(self)

            def none_to_empty(data):
                """
                Return empty string if data is None else return data.
                """
                return data if data is not None else ''
            edit_fields = (
                (field, none_to_empty(getattr(self, field.name)), validator)
                for field, validator in (
                    (cls.display_name, 'string'),
                    (cls.points, 'number'),
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
            fragment.initialize_js('StaffGradedXBlock')
            return fragment
        except:  # pragma: NO COVER
            log.error("Don't swallow my exceptions", exc_info=True)
            raise

    @XBlock.json_handler
    def save_sga(self, data, suffix=''):
        # pylint: disable=unused-argument
        """
        Persist block data when updating settings in studio.
        """
        self.display_name = data.get('display_name', self.display_name)

        # Validate points before saving
        points = data.get('points', self.points)
        # Check that we are an int
        try:
            points = int(points)
        except ValueError:
            raise JsonHandlerError(400, 'Points must be an integer')
        # Check that we are positive
        if points < 0:
            raise JsonHandlerError(400, 'Points must be a positive integer')
        self.points = points

        # Validate weight before saving
        weight = data.get('weight', self.weight)
        # Check that weight is a float.
        if weight:
            try:
                weight = float(weight)
            except ValueError:
                raise JsonHandlerError(400, 'Weight must be a decimal number')
            # Check that we are positive
            if weight < 0:
                raise JsonHandlerError(
                    400, 'Weight must be a positive decimal number'
                )
        self.weight = weight

    @XBlock.handler
    def get_staff_grading_data(self, request, suffix=''):
        # pylint: disable=unused-argument
        """
        Return the html for the staff grading view
        """
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

    @XBlock.handler
    def enter_grade(self, request, suffix=''):
        # pylint: disable=unused-argument
        """
        Persist a score for a student given by staff.
        """
        require(self.is_course_staff())
        score = request.params.get('grade', None)
        module = StudentModule.objects.get(pk=request.params['module_id'])
        if score is None:
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

        module.grade = score
        state['comment'] = request.params.get('comment', '')
        module.state = json.dumps(state)
        module.save()
        log.info(
            "enter_grade for course:%s module:%s student:%s",
            module.course_id,
            module.module_state_key,
            module.student.username
        )

        return Response(json_body=self.staff_grading_data())

    @XBlock.handler
    def remove_grade(self, request, suffix=''):
        # pylint: disable=unused-argument
        """
        Reset a students score request by staff.
        """
        require(self.is_course_staff())
        module = StudentModule.objects.get(pk=request.params['module_id'])
        state = json.loads(module.state)
        state['comment'] = ''
        module.grade = 0
        module.state = json.dumps(state)
        module.save()
        log.info(
            "remove_grade for course:%s module:%s student:%s",
            module.course_id,
            module.module_state_key,
            module.student.username
        )
        return Response(json_body=self.staff_grading_data())

    def is_course_staff(self):
        # pylint: disable=no-member
        """
         Check if user is course staff.
        """
        return getattr(self.xmodule_runtime, 'user_is_staff', False)

    def is_instructor(self):
        # pylint: disable=no-member
        """
        Check if user role is instructor.
        """
        return self.xmodule_runtime.get_user_role() == 'instructor'

    def show_staff_grading_interface(self):
        """
        Return if current user is staff and not in studio.
        """
        in_studio_preview = self.scope_ids.user_id is None
        return self.is_course_staff() and not in_studio_preview

    def past_due(self):
        """
        Return whether due date has passed.
        """
        due = get_extended_due_date(self)
        if due is not None:
            return _now() > due
        return False


def _resource(path):  # pragma: NO COVER
    """
    Handy helper for getting resources from our kit.
    """
    data = pkg_resources.resource_string(__name__, path)
    return data.decode("utf8")


def _now():
    """
    Get current date and time.
    """
    return datetime.datetime.utcnow().replace(tzinfo=pytz.utc)


def load_resource(resource_path):  # pragma: NO COVER
    """
    Gets the content of a resource
    """
    resource_content = pkg_resources.resource_string(__name__, resource_path)
    return unicode(resource_content)


def render_template(template_path, context=None):  # pragma: NO COVER
    """
    Evaluate a template by resource path, applying the provided context.
    """
    if context is None:
        context = {}

    template_str = load_resource(template_path)
    template = Template(template_str)
    return template.render(Context(context))


def require(assertion):
    """
    Raises PermissionDenied if assertion is not true.
    """
    if not assertion:
        raise PermissionDenied
