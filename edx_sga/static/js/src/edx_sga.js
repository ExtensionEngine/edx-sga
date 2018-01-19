/* Javascript for StaffGradedAssignmentXBlock. */
function StaffGradedAssignmentXBlock(runtime, element, options) {
    function xblock($, _) {
        var annotatedUrl = runtime.handlerUrl(element, 'download_annotated');
        var downloadAllSubmissionsUrl = runtime.handlerUrl(element, 'download_all_submissions');
        var downloadSubmissionsUrl = runtime.handlerUrl(element, 'download_submissions');
        var downloadUrl = runtime.handlerUrl(element, 'download_assignment');
        var enterGradeUrl = runtime.handlerUrl(element, 'enter_grade');
        var getStaffGradingUrl = runtime.handlerUrl(element, 'get_staff_grading_data');
        var gradingTemplate;
        var removeGradeUrl = runtime.handlerUrl(element, 'remove_grade');
        var staffAnnotatedUrl = runtime.handlerUrl(element, 'staff_download_annotated');
        var staffDownloadUrl = runtime.handlerUrl(element, 'staff_download');
        var staffUploadUrl = runtime.handlerUrl(element, 'staff_upload_annotated');
        var template = _.template($(element).find("#sga-tmpl").text());
        var updateGradesPublishedUrl = runtime.handlerUrl(element, 'update_grades_published');
        var uploadUrl = runtime.handlerUrl(element, 'upload_assignment');

        function render(state) {
            // Add download urls to template context
            state.downloadUrl = downloadUrl;
            state.annotatedUrl = annotatedUrl;
            state.error = state.error ? state.error : false;
            state.gradesPublished = options.gradesPublished;

            // Render template
            var content = $(element).find("#sga-content").html(template(state));

            // Save grades published value when checkbox clicked
            $('input[name=grades-published]', element).change(function() {
                var url = updateGradesPublishedUrl + '?grades_published=' + this.checked;

                $.get(url, function() {
                    console.log('Student grade visibility updated.');
                }).fail(function() {
                    alert('Something went wrong. Please contact the support team.');
                })
            });

            // Set up file upload
            $(content).find(".fileupload").fileupload({
                url: uploadUrl,
                add: function(e, data) {
                    var do_upload = $(content).find(".upload").html('');
                    $('<button/>')
                        .text('Upload ' + data.files[0].name)
                        .appendTo(do_upload)
                        .click(function() {
                            do_upload.text("Uploading...");
                            data.submit();
                        });
                },
                progressall: function(e, data) {
                    var percent = parseInt(data.loaded / data.total * 100, 10);
                    $(content).find(".upload").text(
                        "Uploading... " + percent + "%");
                },
                fail: function(e, data) {
                    /**
                     * Nginx and other sanely implemented servers return a
                     * "413 Request entity too large" status code if an
                     * upload exceeds its limit.  See the 'done' handler for
                     * the not sane way that Django handles the same thing.
                     */
                    if (data.jqXHR.status == 413) {
                        /* I guess we have no way of knowing what the limit is
                         * here, so no good way to inform the user of what the
                         * limit is.
                         */
                        state.error = "The file you are trying to upload is too large."
                    }
                    else {
                        // Suitably vague
                        state.error = "There was an error uploading your file.";

                        // Dump some information to the console to help someone
                        // debug.
                        console.log("There was an error with file upload.");
                        console.log("event: ", e);
                        console.log("data: ", data);
                    }
                    render(state);
                },
                done: function(e, data) {
                    /* When you try to upload a file that exceeds Django's size
                     * limit for file uploads, Django helpfully returns a 200 OK
                     * response with a JSON payload of the form:
                     *
                     *   {'success': '<error message'}
                     *
                     * Thanks Obama!
                     */
                    if (data.result.success !== undefined) {
                        // Actually, this is an error
                        state.error = data.result.success;
                        render(state);
                    }
                    else {
                        // The happy path, no errors
                        render(data.result);
                    }
                }
            });
        }

        function renderStaffGrading(data) {
            if (data.hasOwnProperty('error')) {
              gradeFormError(data['error']);
            } else {
              gradeFormError('');
              $('.grade-modal').hide();
            }

            if (data.display_name !== '') {
                $('.sga-block .display_name').html(data.display_name);
            }

            // Add download urls to template context
            data.downloadUrl = staffDownloadUrl;
            data.annotatedUrl = staffAnnotatedUrl;

            // Render template
            $(element).find("#grade-info")
                .html(gradingTemplate(data))
                .data(data);

            // Map data to table rows
            data.assignments.map(function(assignment) {
                $(element).find("#grade-info #row-" + assignment.module_id)
                    .data(assignment);
            });

            // Set up grade entry modal
            $(element).find(".enter-grade-button")
                .leanModal({closeButton: "#enter-grade-cancel"})
                .on("click", handleGradeEntry);

            // Set up annotated file upload
            $(element).find("#grade-info .fileupload").each(function() {
                var row = $(this).parents("tr");
                var url = staffUploadUrl + "?module_id=" + row.data("module_id");
                $(this).fileupload({
                    url: url,
                    progressall: function(e, data) {
                        var percent = parseInt(data.loaded / data.total * 100, 10);
                        row.find(".upload").text("Uploading... " + percent + "%");
                    },
                    done: function(e, data) {
                        // Add a time delay so user will notice upload finishing
                        // for small files
                        setTimeout(
                            function() { renderStaffGrading(data.result); },
                            3000)
                    }
                });
            });

            // Submission download variables
            var $submissionCheckboxes = $(".submission-checkbox", element);
            var $submissionFilenames = $(".submission-filename", element);
            var $downloadSelectedSubmissionsButton = $("button#download-selected-submissions", element);
            var $downloadAllSubmissionsButton = $("button#download-all-submissions", element);

            // Set up submission download links, checkboxes and buttons
            $submissionFilenames.on("click", function () {
                showSubmissionDownloadedCheckmark($(this));
            });
            $submissionCheckboxes.on("change", function() {
                if ($submissionCheckboxes.filter(":checked").length > 0) {
                    $downloadSelectedSubmissionsButton.prop("disabled", false);
                }
                else {
                    $downloadSelectedSubmissionsButton.prop("disabled", true);
                }
            });
            if ($submissionCheckboxes.length > 0) {
                $downloadAllSubmissionsButton.prop("disabled", false);
            }
            // Fix for "Download selected submissions" button when re-rendering SGA modal content
            $submissionCheckboxes.trigger("change");

            $downloadSelectedSubmissionsButton.on("click", function() {
                var student_ids = [];
                $submissionCheckboxes.filter(":checked").each(function() {
                    var $submissionFilename = $(this).closest("tr").find(".submission-filename");
                    showSubmissionDownloadedCheckmark($submissionFilename);
                    student_ids.push($(this).val());
                });
                $.post(downloadSubmissionsUrl, JSON.stringify({"student_ids": student_ids})).done(function(data) {
                    window.location = window.location.origin + data.zip_url;
                });
            });
            $downloadAllSubmissionsButton.on("click", function() {
                $submissionFilenames.each(function() {
                    showSubmissionDownloadedCheckmark($(this));
                });
                $.get(downloadAllSubmissionsUrl).done(function (data) {
                    window.location = window.location.origin + data.zip_url;
                });
            });
        }

        function showSubmissionDownloadedCheckmark($submissionFilename) {
            if ($submissionFilename.prev(".submission-downloaded").hasClass("hidden")) {
                $submissionFilename.prev(".submission-downloaded").removeClass("hidden");
            }
        }

        /* Just show error on enter grade dialog */
        function gradeFormError(error) {
            var form = $(element).find("#enter-grade-form");
            form.find('.error').html(error);
        }

        /* Click event handler for "enter grade" */
        function handleGradeEntry() {
            var row = $(this).parents("tr");
            var form = $(element).find("#enter-grade-form");
            $(element).find("#student-name").text(row.data("fullname"));
            form.find("#module_id-input").val(row.data("module_id"));
            form.find("#submission_id-input").val(row.data("submission_id"));
            form.find("#grade-input").val(row.data("score"));
            form.find("#comment-input").text(row.data("comment"));
            form.off("submit").on("submit", function(event) {
                var max_score = row.parents("#grade-info").data("max_score");
                var score = Number(form.find("#grade-input").val());
                event.preventDefault();
                if (!score) {
                    gradeFormError('<br/>Grade must be a number.');
                } else if (score !== parseInt(score)) {
                    gradeFormError('<br/>Grade must be an integer.');
                } else if (score < 0) {
                    gradeFormError('<br/>Grade must be positive.');
                } else if (score > max_score) {
                    gradeFormError('<br/>Maximum score is ' + max_score);
                } else {
                    // No errors
                    $.post(enterGradeUrl, form.serialize())
                        .success(renderStaffGrading);
                }
            });
            form.find('#remove-grade').on('click', function(event) {
                var url = removeGradeUrl + '?module_id=' +
                    row.data('module_id') + '&student_id=' +
                    row.data('student_id');
                event.preventDefault();
                if (row.data('score')) {
                  // if there is no grade then it is pointless to call api.
                  gradeFormError('');
                  $.get(url).success(renderStaffGrading);
                } else {
                    gradeFormError('<br/>No grade to remove.');
                }
            });
            form.find("#enter-grade-cancel").on("click", function() {
                /* We're kind of stretching the limits of leanModal, here,
                 * by nesting modals one on top of the other.  One side effect
                 * is that when the enter grade modal is closed, it hides
                 * the overlay for itself and for the staff grading modal,
                 * so the overlay is no longer present to click on to close
                 * the staff grading modal.  Since leanModal uses a fade out
                 * time of 200ms to hide the overlay, our work around is to
                 * wait 225ms and then just "click" the 'Grade Submissions'
                 * button again.  It would also probably be pretty
                 * straightforward to submit a patch to leanModal so that it
                 * would work properly with nested modals.
                 *
                 * See: https://github.com/mitodl/edx-sga/issues/13
                 */
                setTimeout(function() {
                    $('#grade-submissions-button').click();
                    gradeFormError('');
                }, 225);
            });
        }

        $(function($) { // onLoad
            var block = $(element).find(".sga-block");
            var state = block.attr("data-state");
            render(JSON.parse(state));

            var is_staff = block.attr("data-staff") == "True";
            if (is_staff) {
                gradingTemplate = _.template(
                    $(element).find("#sga-grading-tmpl").text());
                block.find("#grade-submissions-button")
                    .leanModal()
                    .on("click", function() {
                        $.ajax({
                            url: getStaffGradingUrl,
                            success: renderStaffGrading
                        });
                    });
                block.find("#staff-debug-info-button")
                    .leanModal();
            }
        });
    }

    if (require === undefined) {
        /**
         * The LMS does not use require.js (although it loads it...) and
         * does not already load jquery.fileupload.  (It looks like it uses
         * jquery.ajaxfileupload instead.  But our XBlock uses
         * jquery.fileupload.
         */
        if (jQuery().fileupload === undefined) {
            function loadjs(url) {
                $("<script>")
                    .attr("type", "text/javascript")
                    .attr("src", url)
                    .appendTo(element);
            }
            loadjs("/static/js/vendor/jQuery-File-Upload/js/jquery.iframe-transport.js");
            loadjs("/static/js/vendor/jQuery-File-Upload/js/jquery.fileupload.js");
        }
        xblock($, _);
    }
    else {
        /**
         * Studio, on the other hand, uses require.js and already knows about
         * jquery.fileupload.
         */
        require(["jquery", "underscore", "jquery.fileupload"], xblock);
    }
}
