import time

if __package__:
    from .database import create_database, log_activity
    from .tracker import get_active_window_title
else:
    from database import create_database, log_activity
    from tracker import get_active_window_title

def run_tracker():
# Check if the database exists and create it if it doesn't

    create_database()

    print("Starting the activity tracker... Press Ctrl+C to stop.")

    current_app_name, current_window_title = get_active_window_title()
    start_time = time.time()

    while True:
        try:
# Sleep 1 S to it doesn't use too much CPU       
            time.sleep(1) 

            new_app, new_title = get_active_window_title()

#If user switched to a diff app
            if new_app != current_app_name or new_title != current_window_title:
                end_time = time.time()
                duration = end_time - start_time 

                #Save the previous app's data
                log_activity(
                    application_name=current_app_name,
                    window_title=current_window_title,
                    duration_seconds=duration,
                    )

                print(f"Logged: [{current_app_name}] [{current_window_title}] for {round(duration, 1)} seconds.")

#Reset the tracker for the new focused window

                current_app_name = new_app
                current_window_title = new_title
                start_time = time.time()

        except KeyboardInterrupt:
#Exit the app using Ctrl+C in terminal
            end_time = time.time()
            duration = end_time - start_time
            log_activity(current_app_name, current_window_title, duration)
            print("\nTime tracker stopped. Goodbye!")
            break
if __name__ == "__main__":
    run_tracker()