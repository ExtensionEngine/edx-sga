function StaffGradedAssignmentXBlockStudio(runtime, element, server) {
    var saveUrl = runtime.handlerUrl(element, 'save_sga');

    var validators = {
        'number': function(x) {
            return Number(x);
        },
        'required': function(x) {
            return x !== '';
        },
        'string': function(x) {
            return !x ? null : x;
        }
    };

    function validate(data) {
        const weight = data.weight;
        var errors = [];

        if (!validators.required(weight))
            errors.push({
                element: 'weight',
                message: 'Weight is required'
            });
        else if (!validators.number(weight) || weight <= 0)
            errors.push({
                element: 'weight',
                message: 'Weight must be a positive number'
            });
            
        return errors;
    }

    function displayValidationError(errors) {
        var error;
        for (index in errors) {
            error = errors[index];
            $(element).find('#error-' + error.element).html(error.message);
        }
    }

    function save() {
        var view = this;

        var data = {};
        $(element).find('input').each(function(index, input) {
            data[input.name] = input.value;
        });

        var validationErrors = validate(data);

        if (validationErrors.length > 0) {
            displayValidationError(validationErrors);
            return;
        } else {
            view.runtime.notify('save', {state: 'start'});

            $.ajax({
                type: "POST",
                url: saveUrl,
                data: JSON.stringify(data),
                success: function() {
                    view.runtime.notify('save', {state: 'end'});
                },
                error: function(err) {
                    console.error('Error: ', err);
                }
            }); 
        }  
    }

    return {
        displayValidationError: displayValidationError,
        save: save,
        validate: validate
    }
}
