/* Javascript for StaffGradedXBlock. */
function StaffGradedXBlock(runtime, element) {
    function xblock($, _) {
        var getStaffGradingUrl = runtime.handlerUrl(element, 'get_staff_grading_data');
        var enterGradeUrl = runtime.handlerUrl(element, 'enter_grade');
        var removeGradeUrl = runtime.handlerUrl(element, 'remove_grade');
        var template = _.template($(element).find("#sg-tmpl").text());
        var gradingTemplate;

        function render(state) {
            state.error = state.error || false;
            // Render template
            var content = $(element).find('#sg-content').html(template(state));
        }

        function renderStaffGrading(data) {
            if (data.hasOwnProperty('error')) {
              gradeFormError(data['error']);
            } else {
              gradeFormError('');
              $('.grade-modal').hide();
            }

            if (data.display_name !== '') {
                $('.sg-block .display_name').html(data.display_name);
            }

            // Render template
            $(element).find('#sg-grade-info')
                .html(gradingTemplate(data))
                .data(data);

            // Map data to table rows
            data.assignments.map(function(assignment) {
                $(element).find('#sg-grade-info #row-' + assignment.module_id)
                    .data(assignment);
            });

            // Handle 'Enter grade' button click.
            $(element).find('.sg-enter-grade-button')
                .on('click', handleGradeEntry);

            // Handle 'Remove grade' button click.
            $(element).find('.sg-remove-grade')
                .on('click', removeGrade);

            $.tablesorter.addParser({
              id: 'alphanum',
              is: function(s) {
                return false;
              },
              format: function(s) {
                var str = s.replace(/(\d{1,2})/g, function(a){
                    return pad(a);
                });

                return str;
              },
              type: 'text'
            });

            function pad(num) {
              var s = '00000' + num;
              return s.substr(s.length-5);
            }
            $("#sg-submissions").tablesorter({
                headers: {
                  2: { sorter: "alphanum" },
                  3: { sorter: "alphanum" },
                  6: { sorter: "alphanum" }
                }
            });
            $("#sg-submissions").trigger("update");
            var sorting = [[1,0]];
            $("#sg-submissions").trigger("sorton",[sorting]);
        }

        /* Just show error in the error placeholder. */
        function gradeFormError(error) {
            var form = $(element).find("#sg-enter-grade-form");
            form.find('.error').html(error);
            $('button.sg-save-edit').removeAttr('disabled');
        }

        function closeEditing() {
            $('.value').removeClass('hidden');
            $('.option-btns').removeClass('hidden');
            $('.value-input').addClass('hidden');
            $('.editing-btns').addClass('hidden');
            $('button.sg-save-edit').removeAttr('disabled');
        }

        function removeGrade(event) {
            var $el = $(event.target);
            var $row = $el.parents('tr');
            var url = removeGradeUrl + '?module_id=' +
                $row.data('module_id') + '&student_id=' +
                $row.data('student_id');
            event.preventDefault();
            $el.attr('disabled', 'true');

            if (Number($row.find('.grade .value').data('value'))) {
              // if there is no grade then it is pointless to call api.
              $.get(url).success(renderStaffGrading);
            } else {
                gradeFormError('No grade to remove.');
                $el.removeAttr('disabled');
            }
        }

        /* Click event handler for "enter grade" */
        function handleGradeEntry() {
            var $row = $(this).parents("tr");
            var form = $("#sg-enter-grade-form");

            closeEditing();

            $row.find('.value').addClass('hidden');
            $row.find('.option-btns').addClass('hidden');
            $row.find('.value-input').removeClass('hidden');
            $row.find('.editing-btns').removeClass('hidden');

            $row.find('button.sg-cancel-edit').click(closeEditing);
            $row.find('button.sg-remove-grade').click(removeGrade);

            form.find('#module_id-input').val($row.data('module_id'));
            form.find('#submission_id-input').val($row.data('submission_id'));
            form.off('submit').on('submit', function(event) {
                var max_score = $row.parents('#sg-grade-info').data('max_score');
                var score = Number($row.find('.input-grade').val());
                var comment = $row.find('.input-comment').val();

                event.preventDefault();
                $row.find('button.sg-save-edit').attr('disabled', 'true');

                form.find('#grade-input').val(score);
                form.find('#comment-input').val(comment);

                if (!score && (score !== 0)) {
                    gradeFormError('Grade must be a number.');
                } else if (score !== parseInt(score)) {
                    gradeFormError('Grade must be an integer.');
                } else if (score < 0) {
                    gradeFormError('Grade must be positive.');
                } else if (score > max_score) {
                    gradeFormError('Maximum score is ' + max_score);
                } else {
                    // No errors
                    $.post(enterGradeUrl, form.serialize())
                        .success(renderStaffGrading);
                }
            });
        }

        $(function($) { // onLoad
            var block = $(element).find('.sg-block');
            var state = block.attr('data-state');
            render(JSON.parse(state));

            var is_staff = block.attr('data-staff') == 'True';
            if (is_staff) {
                gradingTemplate = _.template(
                    $(element).find('#sg-grading-tmpl').text());
                block.find('#grade-submissions-button')
                    .leanModal()
                    .on('click', function() {
                        $.ajax({
                            url: getStaffGradingUrl,
                            success: renderStaffGrading
                        });
                    });
                block.find('#staff-debug-info-button')
                    .leanModal();
            }
        });
    }

    if (require === undefined) {
        xblock($, _);
    } else {
        require(['jquery', 'underscore'], xblock);
    }
}
